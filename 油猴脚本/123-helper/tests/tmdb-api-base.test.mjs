// TMDB 替代源回归测试（无法直连 api.themoviedb.org 的用户可在设置里配置第三方反代/镜像）：
// 1) 未配置替代源 → 请求走官方 https://api.themoviedb.org/3；
// 2) 替代源带自定义路径 → 原样使用（反代自己决定路径映射）；
// 3) 替代源只填域名（无路径）→ 自动补 /3（与官方路径结构对齐）；
// 4) 替代源尾部斜杠 → 不产生双斜杠；api_key 照常携带。
// 用法：node 油猴脚本/123-helper/tests/tmdb-api-base.test.mjs
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
const code = slice("// src/tmdb.js", "// src/metadata.js");
const driver = `;
globalThis.__mod = { TmdbClient };
`;
const requestedUrls = [];
const sandbox = {
  console, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  Promise, TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout, clearTimeout,
  fetch: async (url) => {
    requestedUrls.push(String(url));
    return { ok: true, status: 200, json: async () => ({ images: { base_url: "https://image.tmdb.org" } }) };
  }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { TmdbClient } = sandbox.__mod;

const makeClient = (apiBase) => {
  requestedUrls.length = 0;
  return new TmdbClient(() => ({ credential: "test-key", language: "zh-CN", region: "CN", apiBase }));
};

// —— 用例 1：未配置替代源 → 官方地址 ——
{
  const client = makeClient("");
  await client.request("/search/movie", { query: "x" });
  assert.match(requestedUrls[0], /^https:\/\/api\.themoviedb\.org\/3\/search\/movie\?/, `未配置时应走官方源，实际 ${requestedUrls[0]}`);
  assert.match(requestedUrls[0], /api_key=test-key/, "api_key 应照常携带");
}

// —— 用例 2：替代源带自定义路径 → 原样使用 ——
{
  const client = makeClient("https://tmdb.example.com/tmdb3");
  await client.request("/search/movie", { query: "x" });
  assert.match(requestedUrls[0], /^https:\/\/tmdb\.example\.com\/tmdb3\/search\/movie\?/, `自定义路径应原样使用，实际 ${requestedUrls[0]}`);
}

// —— 用例 3：替代源只填域名 → 自动补 /3 ——
{
  const client = makeClient("https://tmdb.example.com");
  await client.request("/configuration", {});
  assert.match(requestedUrls[0], /^https:\/\/tmdb\.example\.com\/3\/configuration\?/, `只填域名应自动补 /3，实际 ${requestedUrls[0]}`);
}

// —— 用例 3b：不带协议头（如 api.tmdb.org）→ 自动补 https:// 再补 /3 ——
{
  const client = makeClient("api.tmdb.org");
  await client.request("/configuration", {});
  assert.match(requestedUrls[0], /^https:\/\/api\.tmdb\.org\/3\/configuration\?/, `不带协议头应自动补 https:// 与 /3，实际 ${requestedUrls[0]}`);
}

// —— 用例 4：替代源尾部斜杠 → 不产生双斜杠 ——
{
  const client = makeClient("https://tmdb.example.com/tmdb3///");
  await client.request("/search/movie", { query: "x" });
  assert.match(requestedUrls[0], /^https:\/\/tmdb\.example\.com\/tmdb3\/search\/movie\?/, `尾部斜杠应被去除，实际 ${requestedUrls[0]}`);
  assert.ok(!requestedUrls[0].includes("//search"), "不应出现双斜杠路径");
}

console.log("tmdb-api-base: 官方源兜底、自定义路径原样、域名自动补 /3、尾斜杠清理，全部符合");
