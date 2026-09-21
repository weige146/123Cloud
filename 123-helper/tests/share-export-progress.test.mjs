// 分享管理页「导出 CSV」链路回归测试：
// 1) listShares 翻页有间隔 + onPage 进度回调（供按钮进度显示）；
// 2) "分享接口请求过于频繁" 限流时按放宽的退避重试并最终成功。
// 用法：node 油猴脚本/tests/share-export-progress.test.mjs
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
  DOMException, BigInt, TextEncoder, TextDecoder,
  setTimeout, clearTimeout, AbortController, URL, URLSearchParams,
  btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123865.com" },
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { Pan123Api } = sandbox.__apiMod;

function makeApi(fetchImpl) {
  const api = new Pan123Api({ host: "https://www.123865.com", retryAttempts: 8 });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = fetchImpl;
  return api;
}
const shareResponse = (list, next) => ({
  ok: true,
  status: 200,
  json: async () => ({ code: 0, data: { InfoList: list, Next: next } })
});
const share = (id) => ({ ShareID: id, ShareName: `分享${id}`, FileIdList: [{ FileId: id }] });

// —— 用例 1：翻页拉全 + onPage 进度回调 + 翻页间隔 ——
{
  const calls = [];
  const api = makeApi((url) => {
    calls.push(String(url));
    if (calls.length === 1) return Promise.resolve(shareResponse([share("1"), share("2")], "100"));
    return Promise.resolve(shareResponse([share("3")], "-1"));
  });
  const progress = [];
  const started = Date.now();
  const shares = await api.listShares("", { onPage: (count, page) => progress.push([count, page]) });
  const elapsed = Date.now() - started;
  assert.equal(shares.length, 3, "两页共 3 条分享应全部收集");
  assert.deepEqual(progress, [[2, 1], [3, 2]], "onPage 应逐页回报累计条数与页码");
  assert.equal(calls.length, 2);
  assert.ok(elapsed >= 500, `翻页之间应有约 600ms 间隔（实际总耗时 ${elapsed}ms）`);
  console.log(`ok listShares 翻页进度回调与翻页间隔（2 页，间隔后总耗时 ${elapsed}ms）`);
}

// —— 用例 2：限流报错重试后成功 ——
{
  const calls = [];
  const api = makeApi((url) => {
    calls.push(String(url));
    if (calls.length === 1) return Promise.resolve({ ok: true, status: 200, json: async () => ({ code: 429, message: "分享接口请求过于频繁" }) });
    return Promise.resolve(shareResponse([share("9")], "-1"));
  });
  const shares = await api.listShares("");
  assert.equal(shares.length, 1, "限流重试后应拿到数据");
  assert.equal(calls.length, 2, "第一次限流、第二次成功");
  console.log("ok 限流响应按放宽退避自动重试并成功");
}

// —— 用例 3：持续限流时重试到上限才抛错（退避上限 12s 生效，用小 attempts 验证机制）——
{
  const api = makeApi(() => Promise.resolve({ ok: true, status: 200, json: async () => ({ code: 429, message: "分享接口请求过于频繁" }) }));
  await assert.rejects(
    () => api.listShares("", { attempts: 2 }),
    /过于频繁/,
    "重试耗尽后应抛出限流错误"
  );
  console.log("ok 持续限流最终抛出错误供 UI 提示");
}

console.log("share-export-progress 全部通过");
