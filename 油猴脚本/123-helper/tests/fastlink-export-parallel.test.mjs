// 秒传导出 / 分享页导出 目录按层并发扫描 回归测试：
// 直接从 123-helper.user.js bundle 中切出 utils/api/fastlink 模块，用带在途计数的假 API 驱动。
// 用法：node 油猴脚本/tests/fastlink-export-parallel.test.mjs
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
globalThis.__fastlink = { collectFastlinkFiles, exportPublicShare };
`;
const storageMap = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123865.com" },
  localStorage: {
    getItem: (key) => (storageMap.has(key) ? storageMap.get(key) : null),
    setItem: (key, value) => storageMap.set(key, String(value)),
    removeItem: (key) => storageMap.delete(key)
  }
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { collectFastlinkFiles, exportPublicShare } = sandbox.__fastlink;

const etag = (n) => String(n).padStart(2, "0").repeat(16);

// 在途计数包装：统计 list 类请求的最大并发数
function trackInFlight(api, methodName) {
  const stats = { inFlight: 0, maxInFlight: 0, calls: 0 };
  const original = api[methodName].bind(api);
  api[methodName] = async (...args) => {
    stats.calls += 1;
    stats.inFlight += 1;
    stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
    try {
      return await original(...args);
    } finally {
      stats.inFlight -= 1;
    }
  };
  return stats;
}

// —— 用例 1/2：秒传导出，同层 6 个子目录应并发扫描 ——
{
  const folders = new Map();
  const folder = (id, name) => ({ id, name, type: 1 });
  const subs = ["s1", "s2", "s3", "s4", "s5", "s6"];
  folders.set("f1", [
    { id: "r0", name: "readme.txt", type: 0, size: 1, etag: etag(9) },
    ...subs.map((id, index) => folder(id, `sub${index + 1}`))
  ]);
  for (const [index, id] of subs.entries()) folders.set(id, [{ id: `f${index}`, name: "movie.mkv", type: 0, size: 100 + index, etag: etag(index) }]);
  const api = {
    async listAll(id) {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return folders.get(String(id)) || [];
    },
    async fileInfos() { return []; }
  };
  const stats = trackInFlight(api, "listAll");
  const files = await collectFastlinkFiles(api, [{ id: "f1", name: "剧集", type: 1 }], {});
  assert.equal(stats.calls, 7, "应恰好列出 7 个目录");
  assert.ok(stats.maxInFlight >= 2, `同层目录应并发扫描（最大在途 ${stats.maxInFlight}）`);
  assert.equal(files.length, 7, "6 个子目录文件 + 根目录文件都应收集到");
  const paths = files.map((item) => item.path).sort();
  assert.ok(paths.includes("剧集/readme.txt") && paths.includes("剧集/sub3/movie.mkv"));
  console.log(`ok 秒传导出按层并发（目录 7 个，最大在途 ${stats.maxInFlight}）`);
}

// —— 用例 3：并发上限受 options.concurrency 约束 ——
{
  const folders = new Map();
  const subs = ["s1", "s2", "s3", "s4", "s5", "s6"];
  folders.set("f1", subs.map((id, index) => ({ id, name: `sub${index + 1}`, type: 1 })));
  for (const [index, id] of subs.entries()) folders.set(id, [{ id: `f${index}`, name: "movie.mkv", type: 0, size: 100, etag: etag(index) }]);
  const api = {
    async listAll(id) {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return folders.get(String(id)) || [];
    },
    async fileInfos() { return []; }
  };
  const stats = trackInFlight(api, "listAll");
  const files = await collectFastlinkFiles(api, [{ id: "f1", name: "剧集", type: 1 }], { concurrency: 2 });
  assert.equal(files.length, 6);
  assert.ok(stats.maxInFlight <= 2, `并发不应超过 options.concurrency=2（实际 ${stats.maxInFlight}）`);
  console.log(`ok 并发上限受 options.concurrency 约束（最大在途 ${stats.maxInFlight}）`);
}

// —— 用例 4：分享页导出（导出 JSON 按钮）同层并发 ——
{
  const folders = new Map([
    ["0", [
      { id: "sf1", name: "sub1", type: 1 }, { id: "sf2", name: "sub2", type: 1 },
      { id: "sf3", name: "sub3", type: 1 }, { id: "sf4", name: "sub4", type: 1 },
      { id: "sr", name: "readme.txt", type: 0, size: 10, etag: etag(5) }
    ]],
    ["sf1", [{ id: "fa", name: "a.mkv", type: 0, size: 100, etag: etag(1) }]],
    ["sf2", [{ id: "fb", name: "b.mkv", type: 0, size: 200, etag: etag(2) }]],
    ["sf3", [{ id: "fc", name: "c.mkv", type: 0, size: 300, etag: etag(3) }]],
    ["sf4", [{ id: "fd", name: "d.mkv", type: 0, size: 400, etag: etag(4) }]]
  ]);
  const api = {
    async listSharedDirectoryContents(id) {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return folders.get(String(id)) || [];
    }
  };
  const stats = trackInFlight(api, "listSharedDirectoryContents");
  const artifact = await exportPublicShare(api, { shareKey: "k", sharePwd: "p" }, {});
  assert.equal(stats.calls, 5, "应恰好列出 5 个目录");
  assert.ok(stats.maxInFlight >= 2, `同层目录应并发扫描（最大在途 ${stats.maxInFlight}）`);
  assert.equal(artifact.fileCount, 5, "4 个子目录文件 + 根目录文件都应收集到");
  console.log(`ok 分享页导出按层并发（目录 5 个，最大在途 ${stats.maxInFlight}）`);
}

console.log("fastlink-export-parallel 全部通过");
