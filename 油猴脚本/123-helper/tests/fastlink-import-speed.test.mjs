// 批量重命名 ~33 文件上限修复 + 秒传导入提速 回归测试：
// 1) /b/api/file/info 静默截断 fileIdList 时 fileInfos 自动补查缺失 ID；
// 2) importFastlink 目录按层并发预建、秒传执行并发不再封顶 5。
// 用法：node 油猴脚本/tests/fastlink-import-speed.test.mjs
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
globalThis.__fastlinkSpeed = { Pan123Api, importFastlink };
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123865.com" },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { Pan123Api, importFastlink } = sandbox.__fastlinkSpeed;

const etag = (n) => String(n).padStart(8, "0").repeat(4);

// —— 1. fileInfos：服务端静默截断 fileIdList 时自动补查缺失 ID ——
{
  const api = new Pan123Api({});
  const allIds = Array.from({ length: 40 }, (_, index) => 1000 + index);
  const calls = [];
  // 模拟坏服务端：单批超过 10 个 ID 时只返回前 10 个（静默截断，不报错）
  api.request = async (method, path, { body }) => {
    assert.equal(method, "POST");
    assert.equal(path, "/b/api/file/info");
    const ids = body.fileIdList.map((item) => item.FileId);
    calls.push(ids.length);
    const returned = ids.slice(0, 10);
    return {
      code: 0,
      data: { InfoList: returned.map((id) => ({ FileId: id, FileName: `文件${id}.mp4`, Type: 0, Size: id, Etag: etag(id), ParentFileId: 1 })) }
    };
  };
  const files = await api.fileInfos(allIds);
  assert.equal(files.length, 40, `应补查回全部 40 个文件，实际 ${files.length}`);
  assert.deepEqual(Array.from(files).map((file) => Number(file.id)).sort((left, right) => left - right), [...allIds]);
  assert.ok(calls.some((size) => size === 1), "缺失的 ID 应逐个补查");
  console.log("ok fileInfos 静默截断时自动补查缺失 ID");
}

// —— 2. fileInfos：服务端行为正常时不产生额外请求 ——
{
  const api = new Pan123Api({});
  const allIds = Array.from({ length: 40 }, (_, index) => 2000 + index);
  const calls = [];
  api.request = async (method, path, { body }) => {
    calls.push(body.fileIdList.length);
    return {
      code: 0,
      data: { InfoList: body.fileIdList.map(({ FileId }) => ({ FileId, FileName: `文件${FileId}.mp4`, Type: 0, Size: FileId, Etag: etag(FileId), ParentFileId: 1 })) }
    };
  };
  const files = await api.fileInfos(allIds);
  assert.equal(files.length, 40);
  assert.deepEqual(calls, [33, 7], "应按 33/批分批且无补查");
  console.log("ok fileInfos 按 33/批分批，正常时无补查");
}

// —— 3. importFastlink：秒传执行并发不再封顶 5 ——
{
  const folders = new Map([["0", []]]);
  const files = [];
  for (let index = 0; index < 60; index += 1) {
    files.push({ path: `文件${String(index).padStart(3, "0")}.mp4`, fileName: `文件${String(index).padStart(3, "0")}.mp4`, etag: etag(index + 1), size: 100 + index });
  }
  let inFlight = 0;
  let peakInFlight = 0;
  const api = {
    calls: { listAll: 0, create: 0, reuse: 0 },
    async findChildFolder() {
      return null;
    },
    async createFolder(parentId, name) {
      this.calls.create += 1;
      return `dir-${parentId}-${name}`;
    },
    async ensurePath(rootId, parts) {
      return `dir-${[rootId, ...parts].join("/")}`;
    },
    async reuseFile() {
      this.calls.reuse += 1;
      inFlight += 1;
      peakInFlight = Math.max(peakInFlight, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return `new-${this.calls.reuse}`;
    }
  };
  const parsed = { commonPath: "", files, usesBase62EtagsInExport: false };
  const run = await importFastlink(api, parsed, "root-1", { concurrency: 16 });
  assert.equal(run.ok, 60);
  assert.equal(run.fail, 0);
  assert.ok(peakInFlight > 5, `秒传并发应突破旧上限 5，实际峰值 ${peakInFlight}`);
  console.log(`ok 秒传执行并发峰值 ${peakInFlight}（>5，60 文件全部成功）`);
}

// —— 4. importFastlink：目录按层并发预建 + 父目录列表复用 ——
{
  // 结构：root/a、root/b 两目录各 30 个文件（路径 a/f00.mp4 …）
  const makeFile = (dir, index) => ({ path: `${dir}/f${String(index).padStart(3, "0")}.mp4`, fileName: `f${String(index).padStart(3, "0")}.mp4`, etag: etag(index + 1), size: index });
  const files = [];
  for (let index = 0; index < 30; index += 1) files.push(makeFile("a", index));
  for (let index = 0; index < 30; index += 1) files.push(makeFile("b", index));
  const api = {
    calls: { listAll: 0, create: 0, reuse: 0 },
    async listAll(parentId) {
      this.calls.listAll += 1;
      return [];
    },
    async findChildFolder(parentId, name, listingCache, signal) {
      const children = await this.listAll(parentId, { signal });
      const found = children.find((file) => Number(file.type) === 1 && String(file.name).toLocaleLowerCase() === String(name).toLocaleLowerCase());
      return found ? String(found.id) : null;
    },
    async ensurePath(rootId, parts, cache = new Map(), signal) {
      let current = String(rootId || "0");
      for (const part of parts.filter(Boolean)) {
        const key = `${current}/${part}`;
        if (!cache.has(key)) cache.set(key, this.createFolder(current, part, signal));
        current = String(await cache.get(key));
      }
      return current;
    },
    async createFolder(parentId, name) {
      this.calls.create += 1;
      return `dir-${parentId}/${name}`;
    },
    async reuseFile() {
      this.calls.reuse += 1;
      return `new-${this.calls.reuse}`;
    }
  };
  const parsed = { commonPath: "", files, usesBase62EtagsInExport: false };
  const run = await importFastlink(api, parsed, "root", { concurrency: 16 });
  assert.equal(run.ok, 60);
  assert.equal(run.fail, 0);
  assert.equal(api.calls.create, 2, "只应创建 a、b 两个目录");
  assert.equal(api.calls.listAll, 2, "listingCache 应让 a、b 各只列一次目录");
  console.log("ok 目录按层预建：2 目录 60 文件，仅 2 次列表 + 2 次建目录");
}

console.log("\n4 passed");
