// request() 接入限速门后的行为回归：撞频控「等门开」不烧常规重试预算、预算耗尽才终局、
// 冷却期内并发一起等、暂停只挂住挂了门的链路。
// 用法：node 油猴脚本/123-helper/tests/request-gate.test.mjs
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
  slice("// src/api.js", "// src/core/categories.js"),
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__mod = { Pan123Api, panApiGates, panRunControl, setApiPaceListener, FASTLANE_HOST };
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://www.123865.com" },
  setTimeout, clearTimeout, AbortController,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { Pan123Api, panApiGates, panRunControl } = sandbox.__mod;

// 把节奏与冷却调快：只缩时长参数，机制不变
function speedUpGates() {
  for (const gate of Object.values(panApiGates)) {
    gate.lanes = {
      default: { baseInterval: 4, maxInterval: 40, jitterMs: 2 },
      page: { baseInterval: 4, maxInterval: 40, jitterMs: 2 },
      canonical: { baseInterval: 4, maxInterval: 40, jitterMs: 2 },
      mirror: { baseInterval: 4, maxInterval: 40, jitterMs: 2 }
    };
    gate.cooldownBaseMs = 40;
    gate.cooldownMaxMs = 120;
    gate.recoverAfterMs = 0;
    gate.reset();
  }
}
speedUpGates();

const makeApi = (fetchImpl, options = {}) => {
  const api = new Pan123Api({ host: "https://www.123865.com", retryAttempts: 6, ...options });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = fetchImpl;
  return api;
};
const jsonResponse = (payload) => ({ ok: true, status: 200, json: async () => payload });
const pageResponse = (count = 1) => jsonResponse({ code: 0, data: { InfoList: Array.from({ length: count }, (unused, index) => ({ FileId: index + 1, FileName: `f${index}.mkv`, Type: 0, Size: 10, Etag: "0101" })), Next: "-1" } });
const throttled = () => jsonResponse({ code: 100011, message: "" });

// —— 用例 1：撞一次 100011 后等门开重试成功，不消耗常规重试预算 ——
{
  speedUpGates();
  let calls = 0;
  const api = makeApi(() => {
    calls += 1;
    return Promise.resolve(calls === 1 ? throttled() : pageResponse());
  });
  const page = await api.listPage("0", 1);
  assert.equal(calls, 2, "撞限一次后应自动等门重试");
  assert.equal(page.files.length, 1, "重试成功应正常返回");
  assert.equal(panApiGates.list.strikes, 1, "门应记录一次风控触发");
  console.log("ok 撞频控等门开自动重试（不烧常规重试预算）");
}

// —— 用例 2：持续频控时按「等门次数」封顶后终局，不再往同一窗口里灌 ——
{
  speedUpGates();
  let calls = 0;
  const api = makeApi(() => {
    calls += 1;
    return Promise.resolve(throttled());
  }, { paceRetryCap: 2, paceWaitBudgetMs: 60000 });
  await assert.rejects(() => api.listPage("0", 1), (error) => String(error.code) === "100011" && error.paceExhausted === true, "预算耗尽应带 paceExhausted 终局上抛");
  assert.equal(calls, 3, `应只打 1 + paceRetryCap 次（实际 ${calls}）`);
  console.log(`ok 频控预算耗尽后终局上抛（共 ${calls} 次请求，不再无限重试）`);
}

// —— 用例 3：冷却是全局的——并发请求一起等，不是各自退避 ——
{
  speedUpGates();
  let calls = 0;
  const api = makeApi(() => {
    calls += 1;
    // 前两次撞限，后面成功
    return Promise.resolve(calls <= 2 ? throttled() : pageResponse());
  }, { paceRetryCap: 4, paceWaitBudgetMs: 60000 });
  const started = Date.now();
  const pages = await Promise.all([api.listPage("0", 1), api.listPage("0", 1)]);
  const elapsed = Date.now() - started;
  assert.ok(pages.every((page) => page.files.length === 1), "两个并发请求最终都应成功");
  assert.ok(elapsed >= 40, `应至少等过一轮全局冷却（实际 ${elapsed}ms）`);
  console.log(`ok 并发请求共用一次全局冷却（等待 ${elapsed}ms）`);
}

// —— 用例 4：暂停只挂住挂了门的链路，恢复后继续；取消仍立即抛出 ——
{
  speedUpGates();
  let calls = 0;
  const api = makeApi(async () => {
    calls += 1;
    return pageResponse();
  });
  panRunControl.pause();
  const running = api.listPage("0", 1);
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(calls, 0, "暂停期间不应发出请求");
  panRunControl.resume();
  const page = await running;
  assert.equal(calls, 1, "恢复后应继续发出请求");
  assert.equal(page.files.length, 1);
  const controller = new AbortController();
  panRunControl.pause();
  const cancelled = api.listPage("0", 1, { signal: controller.signal });
  setTimeout(() => controller.abort(), 10);
  await assert.rejects(() => cancelled, (error) => error.name === "AbortError", "暂停中取消应立刻抛 AbortError");
  panRunControl.resume();
  console.log("ok 暂停/恢复只影响挂了门的请求，取消仍然即时生效");
}

// —— 用例 5：秒传未命中带 fastlinkMiss 标记（不算失败） ——
{
  speedUpGates();
  const api = makeApi(() => Promise.resolve(jsonResponse({ code: 0, data: { Reuse: false } })));
  await assert.rejects(
    () => api.reuseFile({ etag: "0101", fileName: "a.mkv", size: 10 }, "0", null),
    (error) => error.fastlinkMiss === true,
    "云端无可复用文件应标成未命中而非普通失败"
  );
  console.log("ok 秒传未命中带独立标记，导入侧可分桶统计");
}

// —— 用例 6：目录列举每页 500 条 + 翻页安全闸（异常分页不会无限翻页） ——
{
  speedUpGates();
  const urls = [];
  const api = makeApi((url) => {
    urls.push(String(url));
    // 永远返回同一页且 Next 不为 -1：模拟服务端分页语义异常
    return Promise.resolve(jsonResponse({ code: 0, data: { InfoList: [{ FileId: 1, FileName: "a.mkv", Type: 0, Size: 1, Etag: "01" }], Next: "2" } }));
  });
  const files = await api.listAll("0", { maxPages: 5 });
  assert.equal(urls.length, 2, `连续两页内容相同应立即止损（实际 ${urls.length} 次请求）`);
  assert.equal(files.length, 2, "止损前已收到的条目应保留");
  assert.ok(urls[0].includes("limit=500"), "默认每页应提到 500 条");
  console.log(`ok 列举默认每页 500 条且异常分页立即止损（${urls.length} 页）`);
}

console.log("request-gate 全部通过");
