// 秒传扫描断点续传 / 中断抢救 / 转存跳过已导入 回归测试：
// 直接从 123-helper.user.js bundle 中切出 utils 与秒传模块，用假 API 驱动。
// 用法：node 油猴脚本/tests/fastlink-progress.test.mjs
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
globalThis.__fastlink = {
  exportFastlinkItems, collectFastlinkFiles, createFastlinkScanCheckpoint,
  readFastlinkScanCheckpoint, clearFastlinkScanCheckpoint,
  buildFastlinkSalvage, importFastlink, buildFastlinkJson,
  exportPublicShare, createFastlinkShareCheckpoint, readFastlinkShareCheckpoint,
  clearFastlinkShareCheckpoint, buildFastlinkPublicArtifact, parsePublicShareInput,
  Pan123Api
};
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
const { exportFastlinkItems, createFastlinkScanCheckpoint, readFastlinkScanCheckpoint, buildFastlinkSalvage, importFastlink, exportPublicShare, createFastlinkShareCheckpoint, readFastlinkShareCheckpoint, buildFastlinkPublicArtifact, parsePublicShareInput, Pan123Api } = sandbox.__fastlink;

// —— 测试数据：两层目录 + 4 个文件，etag 用 32 位十六进制 ——
const etag = (n) => String(n).padStart(2, "0").repeat(16);
function makeTree() {
  const folders = new Map();
  const file = (id, name, size, value) => ({ id, name, type: 0, size, etag: value, s3KeyFlag: `s3-${id}` });
  const folder = (id, name) => ({ id, name, type: 1 });
  folders.set("f1", [folder("s1", "sub1"), folder("s2", "sub2"), file("fd", "d.txt", 50, etag(4))]);
  folders.set("s1", [file("fa", "a.mkv", 100, etag(1)), file("fb", "b.mkv", 200, etag(2))]);
  folders.set("s2", [file("fc", "c.mp4", 300, etag(3))]);
  return folders;
}
function makeApi(folders, options = {}) {
  const infoById = new Map();
  for (const children of folders.values()) {
    for (const item of children) if (item.type === 0 && item.etag) infoById.set(item.id, { id: item.id, etag: item.etag });
  }
  return {
    calls: { listAll: 0, reuse: 0 },
    failNames: options.failNames || [],
    async listAll(id, opts = {}) {
      this.calls.listAll += 1;
      if (options.abortAfter != null && this.calls.listAll > options.abortAfter) options.controller?.abort();
      if (opts.signal?.aborted) throw new DOMException("aborted", "AbortError");
      return folders.get(String(id)) || [];
    },
    async fileInfos() {
      if (options.noInfos) return [];
      const ids = [...arguments[0]];
      return ids.map((id) => infoById.get(String(id))).filter(Boolean);
    },
    async ensurePath(rootId, parts) {
      return `dir:${[rootId, ...parts].join("/")}`;
    },
    async reuseFile(file) {
      this.calls.reuse += 1;
      if (this.failNames.includes(file.fileName || file.name)) throw new Error("模拟转存失败");
      return `new-${file.fileName}`;
    }
  };
}
const root = { id: "f1", name: "剧集", type: 1 };

// 1. 完整导出：断点在成功后清除，产物内容正确
{
  storageMap.clear();
  const api = makeApi(makeTree());
  const checkpoint = createFastlinkScanCheckpoint([root], {});
  assert.equal(checkpoint.resumed, false);
  const artifacts = await exportFastlinkItems(api, [root], { checkpoint });
  assert.equal(artifacts.length, 1);
  assert.equal(artifacts[0].fileCount, 4);
  assert.equal(artifacts[0].filename, "剧集.123fastlink.json");
  const parsed = JSON.parse(artifacts[0].text);
  assert.deepEqual(parsed.files.map((file) => file.path).sort(), ["d.txt", "sub1/a.mkv", "sub1/b.mkv", "sub2/c.mp4"]);
  assert.equal(api.calls.listAll, 3);
  assert.equal(readFastlinkScanCheckpoint(), null);
  console.log("ok 完整导出并清除断点");
}

// 2. 中断续传：取消后保留断点，重跑只补未扫目录且结果完整
{
  storageMap.clear();
  const folders = makeTree();
  const controller = new AbortController();
  const api = makeApi(folders, { abortAfter: 2, controller });
  const checkpoint = createFastlinkScanCheckpoint([root], {});
  await assert.rejects(
    exportFastlinkItems(api, [root], { checkpoint, signal: controller.signal }),
    (error) => error.name === "AbortError"
  );
  const saved = readFastlinkScanCheckpoint();
  assert.ok(saved, "中断后应保留断点");
  assert.equal(saved.files.length, 3);
  assert.ok(saved.completedFolders.includes("f1") && saved.completedFolders.includes("s1"));

  const api2 = makeApi(folders);
  const checkpoint2 = createFastlinkScanCheckpoint([root], {});
  assert.equal(checkpoint2.resumed, true);
  const artifacts = await exportFastlinkItems(api2, [root], { checkpoint: checkpoint2 });
  assert.equal(artifacts[0].fileCount, 4);
  assert.equal(JSON.parse(artifacts[0].text).files.length, 4);
  assert.equal(api2.calls.listAll, 1, "续传只应补列未完成的 sub2");
  assert.equal(readFastlinkScanCheckpoint(), null);
  console.log("ok 扫描断点续传");
}

// 3. 中断抢救：把已扫描部分导出为合法 JSON；缺 Etag 的剔除而不是报错
{
  storageMap.clear();
  const folders = makeTree();
  const controller = new AbortController();
  const api = makeApi(folders, { abortAfter: 2, controller });
  const checkpoint = createFastlinkScanCheckpoint([root], {});
  await assert.rejects(exportFastlinkItems(api, [root], { checkpoint, signal: controller.signal }), (error) => error.name === "AbortError");
  const saved = readFastlinkScanCheckpoint();
  const artifacts = await buildFastlinkSalvage(makeApi(folders), saved, {});
  assert.equal(artifacts.length, 1);
  assert.equal(artifacts[0].fileCount, 3);
  assert.equal(artifacts[0].item.name, "剧集");
  assert.equal(JSON.parse(artifacts[0].text).files.length, 3);

  saved.files.find((file) => file.id === "fb").etag = "";
  const artifacts2 = await buildFastlinkSalvage(makeApi(folders, { noInfos: true }), saved, {});
  assert.equal(artifacts2[0].fileCount, 2);
  console.log("ok 中断抢救导出");
}

// 4. 转存跳过已导入：失败的只补失败的，全部成功后清除断点
{
  storageMap.clear();
  const folders = makeTree();
  const json = (await exportFastlinkItems(makeApi(folders), [root], {}))[0].text;
  const target = "999";
  const api1 = makeApi(folders, { failNames: ["c.mp4"] });
  const run1 = await importFastlink(api1, json, target, { importProgress: true });
  assert.equal(run1.ok, 3);
  assert.equal(run1.fail, 1);
  assert.equal(run1.skipped, 0);

  const api2 = makeApi(folders, { failNames: ["c.mp4"] });
  const run2 = await importFastlink(api2, json, target, { importProgress: true });
  assert.equal(run2.skipped, 3);
  assert.equal(run2.fail, 1);
  assert.equal(api2.calls.reuse, 1, "只应重试失败的 c.mp4");

  const api3 = makeApi(folders);
  const run3 = await importFastlink(api3, json, target, { importProgress: true });
  assert.equal(run3.ok, 1);
  assert.equal(run3.skipped, 3);
  assert.equal(api3.calls.reuse, 1);

  const api4 = makeApi(folders);
  const run4 = await importFastlink(api4, json, target, { importProgress: true });
  assert.equal(run4.skipped, 0);
  assert.equal(run4.ok, 4);
  assert.equal(api4.calls.reuse, 4, "断点已清除，重跑为幂等全量");
  console.log("ok 转存断点跳过已导入");
}

// 5. 选择变化时自动丢弃过期断点
{
  storageMap.clear();
  const folders = makeTree();
  const controller = new AbortController();
  const api = makeApi(folders, { abortAfter: 1, controller });
  const checkpoint = createFastlinkScanCheckpoint([root], {});
  await assert.rejects(exportFastlinkItems(api, [root], { checkpoint, signal: controller.signal }), (error) => error.name === "AbortError");
  assert.ok(readFastlinkScanCheckpoint());
  const other = createFastlinkScanCheckpoint([{ id: "other", name: "其他", type: 1 }], {});
  assert.equal(other.resumed, false);
  assert.equal(readFastlinkScanCheckpoint(), null);
  console.log("ok 选择变化丢弃过期断点");
}

// —— 分享扫描断点 ——
function makeShareTree() {
  return new Map([
    ["0", [{ id: "sf1", name: "电影", type: 1 }, { id: "sr", name: "readme.txt", type: 0, size: 10, etag: etag(5) }]],
    ["sf1", [{ id: "sf2", name: "4k", type: 1 }, { id: "sfa", name: "a.mkv", type: 0, size: 100, etag: etag(1) }]],
    ["sf2", [{ id: "sfb", name: "b.mkv", type: 0, size: 200, etag: etag(2) }]]
  ]);
}
function makeShareApi(folders, options = {}) {
  const controller = options.controller;
  const calls = { list: 0 };
  return {
    calls,
    async listSharedDirectoryContents(id, shareKey, sharePwd, opts = {}) {
      calls.list += 1;
      if (options.abortAfter != null && calls.list > options.abortAfter) controller?.abort();
      if (opts.signal?.aborted) throw new DOMException("aborted", "AbortError");
      return folders.get(String(id)) || [];
    }
  };
}

// 6. 单分享：中断续传 + 抢救产物
{
  storageMap.clear();
  const folders = makeShareTree();
  const input = { shareKey: "abc123", sharePwd: "9999" };
  const controller = new AbortController();
  const api = makeShareApi(folders, { abortAfter: 2, controller });
  const checkpoint = createFastlinkShareCheckpoint(input, {});
  assert.equal(checkpoint.resumed, false);
  await assert.rejects(
    exportPublicShare(api, input, { checkpoint, signal: controller.signal }),
    (error) => error.name === "AbortError"
  );
  const saved = readFastlinkShareCheckpoint();
  assert.ok(saved, "中断后应保留分享断点");
  assert.equal(saved.files.length, 2);
  assert.ok(saved.completedFolders.includes("0") && saved.completedFolders.includes("sf1"));

  const partialArtifact = buildFastlinkPublicArtifact(saved.files.filter((file) => file.etag), { shareKey: saved.shareKey, sharePwd: saved.sharePwd }, {});
  assert.equal(partialArtifact.fileCount, 2);
  assert.deepEqual(JSON.parse(partialArtifact.text).files.map((file) => file.path).sort(), ["readme.txt", "电影/a.mkv"]);

  const api2 = makeShareApi(folders);
  const checkpoint2 = createFastlinkShareCheckpoint(input, {});
  assert.equal(checkpoint2.resumed, true);
  const artifact = await exportPublicShare(api2, input, { checkpoint: checkpoint2 });
  assert.equal(artifact.fileCount, 3);
  assert.equal(api2.calls.list, 1, "续传只应补列未完成的 4k 目录");
  assert.equal(readFastlinkShareCheckpoint(), null);
  console.log("ok 分享扫描断点续传与抢救");
}

// 7. 批量解析断点上下文：按行记录完成数，同上下文可续传，单分享与批量互斥
{
  storageMap.clear();
  const lines = ["https://www.123865.com/s/aaa?pwd=111", "https://www.123865.com/s/bbb", "https://www.123865.com/s/ccc"];
  const input1 = parsePublicShareInput(lines[0]);
  const cp1 = createFastlinkShareCheckpoint(input1, {}, { lines, done: 0 });
  assert.equal(cp1.state.batch.done, 0);
  cp1.state.files.push({ id: "x", path: "a.mkv", etag: etag(1), size: 1 });
  cp1.save(true);

  const input2 = parsePublicShareInput(lines[1]);
  const cp2 = createFastlinkShareCheckpoint(input2, {}, { lines, done: 1 });
  assert.equal(cp2.resumed, false);
  assert.equal(cp2.state.batch.done, 1);
  assert.equal(cp2.state.files.length, 0);

  cp2.state.files.push({ id: "y", path: "b.mkv", etag: etag(2), size: 2 });
  cp2.save(true);
  const cp2b = createFastlinkShareCheckpoint(input2, {}, { lines, done: 1 });
  assert.equal(cp2b.resumed, true, "同行中断应续传");
  assert.equal(cp2b.state.files.length, 1);

  const cpSingle = createFastlinkShareCheckpoint({ shareKey: "zzz" }, {});
  assert.equal(cpSingle.resumed, false);
  assert.equal(readFastlinkShareCheckpoint(), null, "单分享与批量断点互斥");
  console.log("ok 批量解析断点上下文");
}

// 8. 秒传上传遇到 Reuse：旧文件在别的目录时自动挪到请求的目录
{
  const makeUploadApi = (reuseParentId) => {
    const api = new Pan123Api();
    const calls = { move: [], info: 0 };
    api.request = async (method, path, options = {}) => {
      if (path === "/b/api/file/upload_request") return { data: { Reuse: true, FileId: "777" } };
      if (path === "/b/api/file/info") {
        calls.info += 1;
        return { data: { InfoList: [{ FileId: 777, FileName: "s.123fastlink.json", ParentFileId: reuseParentId, Etag: etag(9), Size: 12, Type: 0 }] } };
      }
      if (path === "/b/api/file/mod_pid") {
        calls.move.push(Number(options.body.parentFileId));
        return {};
      }
      throw new Error(`unexpected request: ${path}`);
    };
    return { api, calls };
  };
  // 旧种子在根目录(0)，请求目录是 34463485 → 应自动移动
  const moved = makeUploadApi(0);
  const result1 = await moved.api.uploadTextFile("s.123fastlink.json", "{}", "34463485");
  assert.equal(result1.reused, true);
  assert.deepEqual(moved.calls.move, [34463485]);
  // 旧种子已在目标目录 → 不应多余移动
  const inPlace = makeUploadApi("34463485");
  const result2 = await inPlace.api.uploadTextFile("s.123fastlink.json", "{}", "34463485");
  assert.equal(result2.reused, true);
  assert.deepEqual(inPlace.calls.move, []);
  console.log("ok 秒传 Reuse 自动归位到种子文件夹");
}

console.log("fastlink-progress.test.mjs 全部通过");
