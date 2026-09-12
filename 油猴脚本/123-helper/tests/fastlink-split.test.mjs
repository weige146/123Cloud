// 秒传拆分 splitFastlink 回归测试：从 bundle 中切出 fastlink 模块，验证
// 1.3.7 新增的「按季集/剧名」拆分（tmdb 标记 / 纯季标记 / 无标记三种路径）
// 以及分组语义文件名（123FastLink_分组名_part_N.json）与旧有 folder/count 拆分不回归。
// 用法：node 油猴脚本/123-helper/tests/fastlink-split.test.mjs
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
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__split = { splitFastlink };
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
const { splitFastlink } = sandbox.__split;

const ETAG = "A".repeat(32);
const json = (files, commonPath = "") => JSON.stringify({ commonPath, files: files.map((f) => ({ etag: ETAG, ...f })) });

// —— 用例 1：带 {tmdb-N} 标记 + Season 子目录，同作品各季并入、不同作品分开 ——
{
  const value = json([
    { path: "剧集/{tmdb-123-漫长的季节}/Season 1/E01.mkv", size: 1 },
    { path: "剧集/{tmdb-123-漫长的季节}/Season 1/E02.mkv", size: 2 },
    { path: "剧集/{tmdb-123-漫长的季节}/Season 2/E01.mkv", size: 3 },
    { path: "剧集/{tmdb-456-庆余年}/Season 1/E01.mkv", size: 4 }
  ], "剧集");
  const groups = splitFastlink(value, "work", 1);
  assert.equal(groups.length, 3);
  const names = Array.from(groups.map((g) => g.filename)).sort();
  // tmdb 标记从分组名剥离，季标记保留
  assert.deepEqual(names, [
    "123FastLink_庆余年_S01_part_3.json",
    "123FastLink_漫长的季节_S01_part_1.json",
    "123FastLink_漫长的季节_S02_part_2.json"
  ].sort());
  const total = groups.reduce((sum, g) => sum + g.fileCount, 0);
  assert.equal(total, 4);
}

// —— 用例 2：无 tmdb 标记，用 S01 季标记识别作品+季，顶层目录当剧名 ——
{
  const value = json([
    { path: "咱爸咱妈.Zan.Ba.Zan.Ma.1995.S01/E01.mkv", size: 1 },
    { path: "咱爸咱妈.Zan.Ba.Zan.Ma.1995.S01/E02.mkv", size: 2 },
    { path: "另一部剧.S02/E01.mkv", size: 3 }
  ]);
  const groups = splitFastlink(value, "work", 1);
  assert.equal(groups.length, 2);
  assert.deepEqual(Array.from(groups.map((g) => g.filename)).sort(), [
    "123FastLink_咱爸咱妈.Zan.Ba.Zan.Ma.1995_S01_part_1.json",
    "123FastLink_另一部剧_S02_part_2.json"
  ].sort());
}

// —— 用例 3：完全无季集标记，退化为按一级目录一个作品 ——
{
  const value = json([
    { path: "电影A/file.mkv", size: 1 },
    { path: "电影A/sub/nfo.nfo", size: 2 },
    { path: "电影B/file.mkv", size: 3 },
    { path: "根文件.mkv", size: 4 }
  ]);
  const groups = splitFastlink(value, "work", 1);
  assert.equal(groups.length, 3);
  const byName = Object.fromEntries(Array.from(groups.map((g) => [g.filename, g.fileCount])));
  assert.deepEqual(byName, {
    "123FastLink_电影A_part_1.json": 2,
    "123FastLink_电影B_part_2.json": 1,
    "123FastLink_根目录_part_3.json": 1
  });
}

// —— 用例 4：folder 拆分文件名带目录名，count 保持通用命名 ——
{
  const value = json([
    { path: "第一季/E01.mkv", size: 1 },
    { path: "第二季/E01.mkv", size: 2 }
  ]);
  const folderGroups = splitFastlink(value, "folder", 1);
  assert.deepEqual(Array.from(folderGroups.map((g) => g.filename)), [
    "123FastLink_第一季_part_1.json",
    "123FastLink_第二季_part_2.json"
  ]);
  const countGroups = splitFastlink(value, "count", 1);
  assert.deepEqual(Array.from(countGroups.map((g) => g.filename)), [
    "123FastLink_part_1.json",
    "123FastLink_part_2.json"
  ]);
}

// —— 用例 5：work 拆分产出的 JSON 内容可再解析、文件数守恒 ——
{
  const value = json([
    { path: "剧/{tmdb-9}/S01/E01.mkv", size: 10 },
    { path: "剧/{tmdb-9}/S01/E02.mkv", size: 20 }
  ], "剧");
  const groups = splitFastlink(value, "work", 1);
  assert.equal(groups.length, 1);
  const reparsed = splitFastlink(groups[0].text, "count", 1);
  assert.equal(reparsed.length, 2);
  assert.equal(reparsed.reduce((sum, g) => sum + g.fileCount, 0), 2);
}

console.log("fastlink-split tests passed");
