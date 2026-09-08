// 网盘接口 100011 频控重试回归测试（2026-09-08 实测：file/list/new 约 15 QPS/用户，
// 超速返回 HTTP 200 + {"code":100011,"message":""}，空文案不会被旧重试逻辑识别，
// 会直接中止导出/导入扫描）：
// 1) listPage 收到 100011 后按退避重试，下一次成功则整个调用成功；
// 2) retryAttempts 耗尽仍 100011 时才把错误抛给上层。
// 用法：node 油猴脚本/123-helper/tests/list-100011-retry.test.mjs
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
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123865.com" },
  setTimeout, clearTimeout, AbortController, URL, URLSearchParams,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { Pan123Api } = sandbox.__apiMod;

const makeApi = (fetchImpl) => {
  const api = new Pan123Api({ host: "https://www.123865.com", retryAttempts: 8 });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = fetchImpl;
  return api;
};
const okResponse = () => ({ ok: true, status: 200, json: async () => ({ code: 0, data: { InfoList: [{ FileId: 1, FileName: "a.mkv", Type: 0, Size: 10, Etag: "0202" }], Next: "-1" } }) });
const throttledResponse = () => ({ ok: true, status: 200, json: async () => ({ code: 100011, message: "" }) });

// —— 用例 1：100011 退避重试后成功 ——
{
  let calls = 0;
  const api = makeApi(() => {
    calls += 1;
    return Promise.resolve(calls === 1 ? throttledResponse() : okResponse());
  });
  const page = await api.listPage("0", 1);
  assert.equal(calls, 2, "100011 后应重试一次");
  assert.equal(page.files.length, 1, "重试成功后应正常返回目录页");
  console.log("ok 100011 频控自动退避重试并成功");
}

// —— 用例 2：持续 100011 时重试到上限才抛错 ——
{
  let calls = 0;
  const api = makeApi(() => {
    calls += 1;
    return Promise.resolve(throttledResponse());
  });
  await assert.rejects(
    () => api.listPage("0", 1, { limit: 5 }),
    (error) => error.code === "100011" || /请求失败/.test(String(error.message)),
    "重试耗尽后应抛出频控错误"
  );
  assert.ok(calls >= 4, `应重试多次（实际 ${calls} 次）`);
  console.log(`ok 持续 100011 重试到上限才抛错（${calls} 次）`);
}

console.log("list-100011-retry 全部通过");
