// 自适应限速门（createApiGate）回归测试：并发全局间距、AIMD 降速/提速、快照往返、车道切换。
// 这些参数是「秒传导入/导出不再报请求过于频繁」的核心机制，改动门的行为必须过这里。
// 用法：node 油猴脚本/123-helper/tests/gate-pacing.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const lines = fs.readFileSync(scriptPath, "utf8").split("\n");
const slice = (fromMarker, toMarker) => {
  const start = lines.findIndex((line) => line.includes(fromMarker));
  const end = lines.findIndex((line) => line.includes(toMarker));
  if (start < 0 || end <= start) throw new Error(`bundle markers not found: ${fromMarker} .. ${toMarker}`);
  return lines.slice(start, end).join("\n");
};
const code = [
  slice("// src/core/utils.js", "// src/api.js"),
  slice("// src/api.js", "// src/core/categories.js")
].join("\n");
const driver = `;
globalThis.__mod = { createApiGate, panApiGates, shareApiGate, CANONICAL_SHARE_ORIGIN, gateForPath, isRateLimitedError, driveRouteLabel };
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams,
  location: { origin: "https://www.123pan.cn" },
  setTimeout, clearTimeout, AbortController,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { createApiGate, panApiGates, shareApiGate, gateForPath, isRateLimitedError, CANONICAL_SHARE_ORIGIN } = sandbox.__mod;

// —— 用例 1：并发请求被压成「全局最小间距」，并发数不再等于瞬时 QPS ——
{
  const gate = createApiGate({ name: "test", lanes: { default: { baseInterval: 20, maxInterval: 400, jitterMs: 1 } } });
  const starts = [];
  await Promise.all(Array.from({ length: 8 }, async () => {
    await gate.waitTurn();
    starts.push(Date.now());
  }));
  const gaps = starts.slice(1).map((value, index) => value - starts[index]);
  assert.ok(gaps.every((gap) => gap >= 15), `相邻取号应至少隔一个 interval（实测 ${gaps.join(",")}ms）`);
  console.log(`ok 并发取号被压成全局间距（8 路并发最小间隔 ${Math.min(...gaps)}ms）`);
}

// —— 用例 2：撞限后乘性减速并进入冷却，冷却未结束前所有请求一起等 ——
{
  const gate = createApiGate({ name: "test", cooldownBaseMs: 60, cooldownMaxMs: 500, decreaseFactor: 3, lanes: { default: { baseInterval: 5, maxInterval: 300, jitterMs: 1 } } });
  const waited = gate.hit();
  assert.ok(waited >= 55, `hit 应返回冷却剩余毫秒（实际 ${waited}）`);
  assert.equal(gate.interval, 15, "decreaseFactor=3：间距应放大 3 倍");
  assert.equal(gate.strikes, 1, "hit 应记一次风控触发");
  const started = Date.now();
  await gate.waitTurn();
  assert.ok(Date.now() - started >= 50, "冷却期间取号必须等待");
  const second = gate.hit();
  assert.ok(second > waited, "连续撞限的冷却应指数翻倍");
  console.log(`ok 撞限全局冷却并放慢间距（首次 ${waited}ms → 二次 ${second}ms）`);
}

// —— 用例 3：恢复要谨慎（连续成功足够多次 + 距上次撞限超过恢复窗口才提速）——
{
  const gate = createApiGate({ name: "test", cooldownBaseMs: 10, cooldownMaxMs: 40, recoverAfterMs: 30, okStreakTarget: 4, decreaseFactor: 3, lanes: { default: { baseInterval: 5, maxInterval: 300, jitterMs: 1 } } });
  gate.hit();
  for (let index = 0; index < 3; index += 1) gate.ok();
  assert.equal(gate.interval, 15, "成功次数不够时不该提速");
  await new Promise((resolve) => setTimeout(resolve, 40));
  gate.ok();
  gate.ok();
  gate.ok();
  gate.ok();
  assert.ok(gate.interval < 15, `恢复窗口过后应降回更小间距（实际 ${gate.interval}）`);
  console.log(`ok 提速需同时满足连续成功与恢复窗口（${gate.interval}ms）`);
}

// —— 用例 4：snapshot/restore 往返（断点续跑不带刚触发的风控满速再撞） ——
{
  const gate = createApiGate({ name: "test", cooldownBaseMs: 5000, cooldownMaxMs: 20000, lanes: { default: { baseInterval: 80, maxInterval: 1500, jitterMs: 25 } } });
  gate.hit();
  const saved = gate.snapshot();
  const other = createApiGate({ name: "test", cooldownBaseMs: 5000, cooldownMaxMs: 20000, lanes: { default: { baseInterval: 80, maxInterval: 1500, jitterMs: 25 } } });
  other.restore(saved);
  assert.equal(other.interval, saved.interval, "restore 应还原间距");
  assert.equal(other.strikes, saved.strikes, "restore 应还原风控次数");
  assert.ok(other.cooldownUntil > Date.now(), "未到期冷却应带过来");
  other.restore(null);
  console.log("ok 门控水位可随断点持久化并还原");
}

// —— 用例 5：车道按 host 切换（页面域名 / 123865 / 镜像线路配额不同） ——
{
  const listGate = panApiGates.list;
  listGate.reset();
  listGate.configureFor("https://www.123pan.cn");
  const pageInterval = listGate.interval;
  listGate.configureFor(CANONICAL_SHARE_ORIGIN);
  assert.ok(listGate.baseInterval <= pageInterval, "123865 车道的起步间距不该比页面域名更保守");
  const cruisingInterval = listGate.interval;
  listGate.hit();
  const penalised = listGate.interval;
  assert.ok(penalised > cruisingInterval, "撞限后间距应放大");
  listGate.configureFor("https://www.123pan.cn");
  assert.equal(listGate.interval, penalised, "冷却中的惩罚间距不该被换车道抹掉");
  listGate.reset();
  console.log("ok 列举门按域名切车道，冷却中的惩罚不被抹掉");
}

// —— 用例 6：分享门维持旧语义（撞限直接踩到该车道最大间距） ——
{
  assert.equal(shareApiGate.decreaseFactor, 0, "分享门应保持『一步踩到最大间距』的旧行为");
  shareApiGate.lanes.default = { baseInterval: 1000, maxInterval: 2000, jitterMs: 120 };
  shareApiGate.configureFor("https://1849243207.mshare.123pan.cn");
  const before = shareApiGate.interval;
  shareApiGate.hit();
  assert.equal(shareApiGate.interval, shareApiGate.maxInterval, "分享接口撞限后应直接放慢到慢车道上限间距");
  assert.ok(shareApiGate.interval > before || before === shareApiGate.maxInterval, "分享门撞限不应变快");
  shareApiGate.reset();
  console.log("ok 分享接口限速门行为与旧实现一致");
}

// —— 用例 7：按路径自动挑门；分享接口不在自动表里 ——
{
  assert.equal(gateForPath("/b/api/file/list/new"), panApiGates.list, "目录列举应走 list 门");
  assert.equal(gateForPath("/b/api/file/upload_request"), panApiGates.write, "秒传写入应走 write 门");
  assert.equal(gateForPath("/b/api/share/get"), null, "分享接口由调用方显式传门，不能重复挂");
  assert.equal(gateForPath("/b/api/file/rename"), null, "未登记的接口不自动挂门");
  assert.ok(isRateLimitedError({ code: "100011" }), "空文案的 100011 必须识别为频控");
  assert.ok(isRateLimitedError({ message: "分享接口请求过于频繁" }), "频繁文案应识别为频控");
  assert.ok(!isRateLimitedError({ message: "登录已过期" }), "非频控错误不该被当成频控");
  console.log("ok 自动挂门表与频控识别符合预期");
}

console.log("gate-pacing 全部通过");
