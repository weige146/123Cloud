// 秒传 JSON 流式解析回归测试。
// 背景：本地秒传 JSON 达到几百 MB / 几 GB（几十万条文件）时，旧流程 file.text() 整读 +
// 主线程 JSON.parse + 塞回 textarea 渲染会直接卡死页面。1.3.3 起改为 createFastlinkJsonScanner
// 增量字符流解析 + parseFastlinkJsonFileStreaming 分块读取（定期让出主线程）。
// 核心断言：**流式结果与文本路径 parseFastlinkJson 严格一致**（任意切块边界、键序、转义、BOM）。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const lines = readFileSync(join(root, "123-helper.user.js"), "utf8").split("\n");
const start = lines.findIndex((line) => line.includes("function validEtag"));
const end = lines.findIndex((line) => line.includes("async function saveFastlinkFromCloudFile"));
assert.ok(start > 0 && end > start, "无法在脚本中定位流式解析代码段");
const context = vm.createContext({
  cleanPathPart: (value) => String(value).replace(/[#$%]/g, "").trim(),
  base62ToHex: (value) => `b62:${value}`,
  FASTLINK_PREFIXES: {},
  TextDecoder,
  TextEncoder,
  ReadableStream,
  MessageChannel,
  DOMException
});
vm.runInContext(lines.slice(start, end).join("\n"), context);
const { parseFastlinkJson, createFastlinkJsonScanner, parseFastlinkInputFile, importResolvedFastlink } = context;
assert.equal(typeof parseFastlinkJson, "function");
assert.equal(typeof createFastlinkJsonScanner, "function");

// 确定性伪随机：扰动切块边界。
function makeRng(seed) {
  let state = seed;
  return () => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
}

// 跨 vm realm 的对象原型不同，deepStrictEqual 会误报；两边都经同一 normalizeImportedFiles
// 构造、键序确定，用 JSON 字符串比较结构等价。
const same = (left, right) => assert.equal(JSON.stringify(left), JSON.stringify(right));

// 把文本按 1~maxChunk 的随机长度切块喂给扫描器，模拟网络/文件流。
function streamParse(text, { maxChunk = 7, seed = 42 } = {}) {
  const rng = makeRng(seed);
  const scanner = createFastlinkJsonScanner();
  for (let offset = 0; offset < text.length;) {
    const size = Math.min(text.length - offset, 1 + Math.floor(rng() * maxChunk));
    scanner.push(text.slice(offset, offset + size));
    offset += size;
  }
  return scanner.finish();
}

const HEX = "0123456789abcdef";
const hexEtag = (seedText) => {
  let out = "";
  for (let index = 0; index < 32; index += 1) out += HEX[(seedText.charCodeAt(index % seedText.length) + index * 7) % 16];
  return out;
};

// 1. 标准导出形态：{commonPath, files:[...], usesBase62EtagsInExport}
{
  const data = {
    commonPath: "电影/2024/沙丘2/",
    usesBase62EtagsInExport: false,
    files: [
      { path: "沙丘2.Part1.mkv", etag: hexEtag("a1"), size: 8589934592 },
      { path: "字幕/沙丘2.chs.ass", etag: hexEtag("b2"), size: 45678 }
    ]
  };
  const text = JSON.stringify(data);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 2. 键序无关：files 在前、commonPath 在后、usesBase62 夹在中间。
{
  const data = {
    files: [{ path: "a/b/c.mp4", etag: hexEtag("c3"), size: 1 }],
    usesBase62EtagsInExport: false,
    commonPath: "剧集/"
  };
  const text = JSON.stringify(data);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 3. 未知键携带嵌套结构（对象/数组/含花括号引号转义的字符串）必须整体跳过、不污染条目。
{
  const data = {
    tool: "x\"}{[",
    meta: { nested: [1, 2, { deep: "}}\"]" }], note: "可以包含 {} 与 \\\\ 引号" },
    files: [{ path: "视频/正片.mkv", etag: hexEtag("d4"), size: 5 }],
    version: 1,
    tags: ["a}", "b["],
    commonPath: ""
  };
  const text = JSON.stringify(data);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 3b. 真实导出键布局：files 之前有多个未跟踪的字符串/数字/布尔元数据键（影巢官组导出实测），
// 相位机必须在这些值结束后正确回到 key 相位，否则 files 数组不被识别。
{
  const data = {
    scriptVersion: "3.2.0-tdr.3",
    exportVersion: "1.0",
    usesBase62EtagsInExport: false,
    commonPath: "影巢官组/",
    totalFilesCount: 87525,
    totalSize: 377075346631853,
    formattedTotalSize: "342.95 TB",
    files: [{ path: "片子/a.mkv", etag: hexEtag("m1"), size: 3444131807 }],
    extra: { note: "尾部未跟踪键" }
  };
  const text = JSON.stringify(data);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 4. 紧凑形态：[[etag,size,path],...] 裸数组。
{
  const triples = [[hexEtag("e5"), 123, "p/f.mp4"], [hexEtag("f6"), 0, "g/h.txt"]];
  const text = JSON.stringify(triples);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 5. fileName 变体键 + emoji/中日文路径 + usesBase62（合法 hex etag 不触发换算）。
{
  const data = {
    commonPath: "🎬合集/",
    usesBase62EtagsInExport: true,
    files: [
      { fileName: "初来乍到 S01E01.mkv", etag: hexEtag("g7"), size: 9 },
      { path: "字幕/繁體.chi.ass", etag: hexEtag("h8"), size: 10 }
    ]
  };
  const text = JSON.stringify(data);
  same(streamParse(text), parseFastlinkJson(JSON.parse(text)));
}

// 6. BOM + 前置空白 + 任意空白分布。
{
  const text = `\uFEFF \r\n\t { "commonPath" : "x/" , "files" : [ { "path" : "a.mkv" , "etag" : "${hexEtag("i9")}" , "size" : 3 } ] }  \n`;
  same(streamParse(text), parseFastlinkJson(JSON.parse(text.trim())));
}

// 7. 错误行为与文本路径一致：空 files / 缺 files 键。
{
  const emptyFiles = JSON.stringify({ commonPath: "a/", files: [] });
  assert.throws(() => streamParse(emptyFiles), /秒传内容没有文件记录/);
  assert.throws(() => parseFastlinkJson(JSON.parse(emptyFiles)), /秒传内容没有文件记录/);
  assert.throws(() => streamParse(JSON.stringify({ commonPath: "a/" })), /秒传内容没有文件记录/);
}

// 8. 截断的 JSON：finish 报结构错误，并被包装成「秒传 JSON 解析失败」。
{
  const scanner = createFastlinkJsonScanner();
  scanner.push(`{"commonPath":"a/","files":[{"path":"a.mkv","etag":"${hexEtag("j1")}"`);
  assert.throws(() => scanner.finish(), (error) => error.name === "FastlinkJsonStructureError" && /不完整/.test(error.message));
}

// 9. files 数组里出现标量条目：快速报错，不静默丢条目。
{
  assert.throws(() => streamParse('{"files":[1,2]}'), /第 1 项不是对象/);
  assert.throws(() => streamParse('["abc",["x","y","z"]]'), /第 1 项不是数组/);
}

// 10. 大批量（2 万条）分块流式：数量与首尾条目正确。
{
  const files = Array.from({ length: 20000 }, (_, index) => ({ path: `剧/S01E${String(index + 1).padStart(2, "0")}.mkv`, etag: hexEtag(`ep${index}`), size: index * 1024 }));
  const text = JSON.stringify({ commonPath: "剧/", files, usesBase62EtagsInExport: false });
  const parsed = streamParse(text, { maxChunk: 65536, seed: 7 });
  assert.equal(parsed.commonPath, "剧/");
  assert.equal(parsed.files.length, 20000);
  same(parsed.files[0], parseFastlinkJson(JSON.parse(text)).files[0]);
  same(parsed.files.at(-1), parseFastlinkJson(JSON.parse(text)).files.at(-1));
}

// 11. parseFastlinkInputFile：JSON 文件走流式（File.stream 分块），进度单调递增到文件大小。
{
  const data = { commonPath: "m/", files: Array.from({ length: 500 }, (_, index) => ({ path: `f${index}.mkv`, etag: hexEtag(`s${index}`), size: index })) };
  const text = JSON.stringify(data);
  const file = fakeFile(text, 512);
  const progress = [];
  const parsed = await parseFastlinkInputFile(file, { onProgress: (bytes) => progress.push(bytes) });
  same(parsed, parseFastlinkJson(JSON.parse(text)));
  assert.ok(progress.length > 1, "应有多帧读取进度");
  assert.equal(progress.at(-1), file.size);
  assert.deepEqual([...progress], [...progress].sort((a, b) => a - b), "进度应单调递增");
}

// 12. parseFastlinkInputFile：非 JSON（V1/V2 文本、.123share、短链接）回落整读 parseFastlink。
{
  const original = context.parseFastlink;
  let called = 0;
  context.parseFastlink = (value) => {
    called += 1;
    return { viaText: value };
  };
  try {
    const file = fakeFile("123FLCPV2$%xxx", 64);
    const parsed = await parseFastlinkInputFile(file, {});
    assert.equal(called, 1);
    assert.deepEqual(parsed, { viaText: "123FLCPV2$%xxx" });
  } finally {
    context.parseFastlink = original;
  }
}

// 13. importResolvedFastlink：单条种子文件走二级还原，多条走普通导入。
{
  const originalImport = context.importFastlink;
  const originalSecondary = context.saveSecondaryFastlink;
  const calls = { import: 0, secondary: 0 };
  context.importFastlink = async () => {
    calls.import += 1;
    return "import";
  };
  context.saveSecondaryFastlink = async () => {
    calls.secondary += 1;
    return "secondary";
  };
  try {
    assert.equal(await importResolvedFastlink({}, { files: [{ fileName: "包.123fastlink.json", etag: hexEtag("k1"), size: 1 }] }, "0", {}), "secondary");
    assert.equal(await importResolvedFastlink({}, { files: [{ fileName: "a.mkv", etag: hexEtag("k2"), size: 1 }, { fileName: "b.mkv", etag: hexEtag("k3"), size: 2 }] }, "0", {}), "import");
    assert.deepEqual(calls, { import: 1, secondary: 1 });
  } finally {
    context.importFastlink = originalImport;
    context.saveSecondaryFastlink = originalSecondary;
  }
}

function fakeFile(text, chunkSize) {
  const bytes = new TextEncoder().encode(text);
  const makeStream = (view) => new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < view.length; offset += chunkSize) controller.enqueue(view.subarray(offset, offset + chunkSize));
      controller.close();
    }
  });
  return {
    size: bytes.byteLength,
    name: "test.123fastlink.json",
    stream: () => makeStream(bytes),
    slice: (sliceStart, sliceEnd) => ({ stream: () => makeStream(bytes.subarray(sliceStart, sliceEnd)) }),
    text: async () => new TextDecoder().decode(bytes)
  };
}

console.log("fastlink-json-stream: 全部断言通过");
