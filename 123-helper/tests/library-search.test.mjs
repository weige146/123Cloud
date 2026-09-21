// 影库搜索（123Cloud 客户端影库接口）辅助函数回归测试：
// 1) libraryBaseUrl / libraryRequestUrl：地址校验、token 自动附带、query 组装；
// 2) buildLibraryFastlinkPayload：勾选文件转秒传 payload（过滤无效 etag/size，commonPath 取作品末段）；
// 3) renderLibraryFileList：勾选状态渲染。
// 用法：node 油猴脚本/tests/library-search.test.mjs
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
  slice("// src/submission-client.js", "// src/ui/components.js"),
].join("\n");
const driver = `;
globalThis.__librarySearch = { librarySearchState, libraryBaseUrl, libraryRequestUrl, buildLibraryFastlinkPayload, renderLibraryFileList, escapeHtml };
`;
const store = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  Promise, Boolean, isNaN, parseFloat, parseInt,
  URL, URLSearchParams,
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  },
  escapeHtml: (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]),
  formatBytes: (value) => `${value}B`,
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox);
const lib = sandbox.__librarySearch;

// --- librarySearchState 默认值 ---
const state = lib.librarySearchState();
assert.equal(state.loaded, false);
assert.equal(state.page, 1);
assert.equal(state.size, 20);
assert.equal(state.results.length, 0);
assert.equal(state.selectedFiles.length, 0);

// --- libraryBaseUrl ---
assert.throws(() => lib.libraryBaseUrl(""), /影库接口地址/);
assert.throws(() => lib.libraryBaseUrl("ftp://x"), /HTTP\/HTTPS|仅支持/);
const parsed = lib.libraryBaseUrl("http://192.168.1.5:8321?token=abc");
assert.equal(parsed.host, "192.168.1.5:8321");

// --- libraryRequestUrl：token 自动附带 + query 组装 ---
const config = { share: { libraryUrl: "http://192.168.1.5:8321", libraryToken: "t".repeat(24) } };
assert.equal(
  lib.libraryRequestUrl(config, "/api/library/search", { q: "海 王", page: 2, size: 20 }),
  "http://192.168.1.5:8321/api/library/search?q=%E6%B5%B7+%E7%8E%8B&page=2&size=20&token=" + "t".repeat(24),
);
// 地址里自带 token 时也认
const inline = lib.libraryRequestUrl(
  { share: { libraryUrl: "http://10.0.0.2:8321/?token=inline123", libraryToken: "" } },
  "/api/library/status",
);
assert.equal(inline, "http://10.0.0.2:8321/api/library/status?token=inline123");
// 空参数不产生 query（有 token 除外）
const noParams = lib.libraryRequestUrl({ share: { libraryUrl: "http://x.a", libraryToken: "" } }, "/api/library/categories");
assert.equal(noParams, "http://x.a/api/library/categories");
// 带路径前缀的 base 也支持（反向代理场景：前缀保留，/api/library/* 拼在其后）
const withPath = lib.libraryRequestUrl({ share: { libraryUrl: "http://x.a/prefix", libraryToken: "" } }, "/api/library/files", { dir: "电影/海王" });
assert.equal(withPath, "http://x.a/prefix/api/library/files?dir=" + encodeURIComponent("电影/海王"));

// --- buildLibraryFastlinkPayload ---
const payload = lib.buildLibraryFastlinkPayload("电影/华语/海王 (2018) {tmdb-297802}", [
  { fileName: "a.mkv", etag: "abc123", size: 5 },
  { fileName: "bad.mkv", etag: "", size: 5 },
  { fileName: "zero.mkv", etag: "xyz", size: 0 },
  { fileName: "b.mp4", etag: "def456", size: 7 },
]);
assert.equal(payload.commonPath, "海王 (2018) {tmdb-297802}");
assert.equal(payload.files.length, 2);
assert.deepEqual(payload.files.map((f) => f.fileName), ["a.mkv", "b.mp4"]);
assert.throws(() => lib.buildLibraryFastlinkPayload("x", [{ fileName: "a", etag: "", size: 0 }]), /没有可秒传/);

// --- renderLibraryFileList ---
const html = lib.renderLibraryFileList({
  filesLoading: false,
  selectedFiles: ["a.mkv"],
  files: [
    { fileName: "a.mkv", size: 5 },
    { fileName: "b.mkv", size: 6 },
  ],
  transferBusy: false,
});
assert.ok(html.includes('data-library-file="a.mkv"'));
assert.ok(html.includes("checked"));
assert.ok(html.includes("转存选中（1）"));

console.log("library-search test: all assertions passed");
