// 分享限速门抖动回归测试（修复：waitTurn 曾硬编码 Math.random()*120ms，
// 快车道 jitterMs:2 的配置从未生效，导致直连 123865 的"零限流"快车道被
// 串行成平均 60ms 间距、吞吐被压到 ~16 req/s，实测 258 req/s 完全吃不到）：
// 1) 快车道（www.123865.com）单次等待 = interval(0) + jitter(0~2ms) 以内；
// 2) 慢车道（页面分享域名）单次等待 = interval(1000) + jitter(0~119ms)。
// 用法：node 油猴脚本/123-helper/tests/share-gate-jitter.test.mjs
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
globalThis.__mod = { shareApiGate, CANONICAL_SHARE_ORIGIN };
`;
// 可控时钟：sleep 推进虚拟时间，waitTurn 的循环才能确定性地走出"等待→放行"。
let fakeNow = 1_700_000_000_000;
class FakeDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(fakeNow);
    else super(...args);
  }
  static now() {
    return fakeNow;
  }
}
const sandbox = {
  console, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise,
  location: { origin: "https://1849243207.mshare.123pan.cn" },
  Date: FakeDate,
  setTimeout, clearTimeout
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
// 注意：必须等 bundle 执行完再替换 sleep——bundle 顶层的 `var sleep = ...` 会覆盖预置值，
// 而 waitTurn 在调用时才解析全局 sleep，事后替换才能让假时钟随等待推进。
sandbox.sleep = async (ms) => {
  fakeNow += Math.max(0, Number(ms) || 0);
};
const { shareApiGate, CANONICAL_SHARE_ORIGIN } = sandbox.__mod;

const sampleWait = async (host, samples = 40) => {
  const waits = [];
  for (let index = 0; index < samples; index += 1) {
    shareApiGate.reset();
    shareApiGate.configureFor(host);
    // 必须用沙箱内的假时钟（fakeNow）而不是本测试进程的 Date.now()——
    // 两个时钟相差数年，会把差值当成一次等待。
    shareApiGate.lastStart = fakeNow;
    const before = fakeNow;
    await shareApiGate.waitTurn(null);
    waits.push(fakeNow - before);
  }
  return waits;
};

// —— 用例 1：快车道抖动 ≤ jitterMs(2ms)，不再吃 120ms 硬编码 ——
{
  const waits = await sampleWait(CANONICAL_SHARE_ORIGIN);
  const max = Math.max(...waits);
  assert.ok(max <= 2, `快车道单次等待应 ≤2ms（jitterMs=2），实测最大 ${max}ms`);
}

// —— 用例 2：慢车道保持 1s 间距 + 最多 120ms 抖动 ——
{
  const waits = await sampleWait("https://1849243207.mshare.123pan.cn");
  const min = Math.min(...waits);
  const max = Math.max(...waits);
  assert.ok(min >= 1000, `慢车道最小等待应 ≥1000ms，实测 ${min}ms`);
  assert.ok(max <= 1119, `慢车道最大等待应 ≤1119ms（1000+119 抖动），实测 ${max}ms`);
}

console.log("share-gate-jitter: 快车道抖动 ≤2ms、慢车道 1000~1119ms，全部符合");
