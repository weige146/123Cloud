// request() 重试签名刷新回归测试（修复：signedQuery 曾在重试循环外只算一次，
// 长退避链（如分享列表 8 次×12s 上限）跨过签名的分钟级时间戳后，旧签名会被
// 服务端拒绝且签名错误不在可重试文案里，直接终局失败）：
// 1) 第一次请求返回 100011 触发退避重试，退避使虚拟时钟跨过 1 分钟；
// 2) 第二次请求必须携带重新计算后的签名（时间戳变化），而不是复用旧签名。
// 用法：node 油猴脚本/123-helper/tests/request-resign-on-retry.test.mjs
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
globalThis.__apiMod = { Pan123Api };
`;
// 可控时钟：每次退避 sleep 直接推进 61 秒，强制签名的时间分钟翻转。
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
const requestedUrls = [];
const sandbox = {
  console, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams,
  location: { origin: "https://www.123865.com" },
  Date: FakeDate,
  setTimeout, clearTimeout, AbortController,
  fetch: async (url) => {
    requestedUrls.push(String(url));
    if (requestedUrls.length === 1) {
      return { ok: true, status: 200, json: async () => ({ code: 100011, message: "" }) };
    }
    return { ok: true, status: 200, json: async () => ({ code: 0, data: { InfoList: [{ FileId: 1, FileName: "a.mkv", Type: 0, Size: 10, Etag: "0202" }], Next: "-1" } }) };
  }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
// 注意：必须等 bundle 执行完再替换 sleep——bundle 顶层的 `var sleep = ...` 会覆盖预置值，
// 而 request() 在调用时才解析全局 sleep，事后替换才能让退避直接推进假时钟（+61s 跨分钟）。
sandbox.sleep = async () => {
  fakeNow += 61_000;
};
const { Pan123Api } = sandbox.__apiMod;

const api = new Pan123Api({ host: "https://www.123865.com", retryAttempts: 6 });
api.credentials = () => ({ token: "t", loginUuid: "u" });

const signedEntries = (url) => {
  const params = new URL(url).searchParams;
  const output = [];
  for (const [key, value] of params.entries()) {
    if (/^\d+(\.\d+)?-\d+-\d+$/.test(value)) output.push([key, value]);
  }
  return output;
};

await api.listPage("0", 1);
assert.equal(requestedUrls.length, 2, "应发起 2 次请求（1 次 100011 + 1 次重试成功）");
const first = signedEntries(requestedUrls[0]);
const second = signedEntries(requestedUrls[1]);
assert.equal(first.length, 1, `第一次请求应恰有 1 个签名参数，实际 ${JSON.stringify(first)}`);
assert.equal(second.length, 1, `重试请求应恰有 1 个签名参数，实际 ${JSON.stringify(second)}`);
assert.notEqual(
  `${first[0][0]}=${first[0][1]}`,
  `${second[0][0]}=${second[0][1]}`,
  "重试必须重新签名：两次请求的签名参数（键与时间戳）不应相同"
);
const firstSeconds = Number(String(first[0][1]).split("-")[0].split(".")[0]);
const secondSeconds = Number(String(second[0][1]).split("-")[0].split(".")[0]);
assert.equal(secondSeconds - firstSeconds, 61, "重试签名时间戳应反映重试时的当前时间（退避推进 61 秒）");

console.log("request-resign-on-retry: 重试后签名按当前时间重新计算，全部符合");
