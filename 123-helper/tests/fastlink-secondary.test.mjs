// 二级秒传与秒传文本上传回归测试：直接从 123-helper.user.js bundle 中切出工具与秒传模块，
// 覆盖 UTF-8 安全 MD5（上游 123FastLink v2026.9.2.1 中文修复）、Base62 往返、
// 二级链接构建/解析、种子文件上传与「保存二级链接 → 转存完整内容」全链路（mock API）。
// 用法：node 油猴脚本/tests/fastlink-secondary.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import crypto from "node:crypto";
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
  slice("// src/core/categories.js", "// src/core/recognition-maps.js"),
  slice("// src/core/recognition-maps.js", "// src/config.js"),
  slice("// src/config.js", "// src/icons.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js"),
  slice("// src/core/table-selection.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__fastlink = {
  md5Hex, stringByteSize, hexToBase62, base62ToHex, validEtag,
  buildFastlinkText, buildFastlinkJson, parseFastlink,
  generateSecondaryFastlink, saveSecondaryFastlink, saveFastlinkFromCloudFile,
  resolveAndImportFastlink, resolveAndImportFastlinkInput, resolveAndImportFastlinkSegments,
  saveFastlinkFromCloudFiles, mergeFastlinkBatchResults,
  splitFastlinkImportPayloads, fastlinkSegmentPreview,
  readFastlinkImportCheckpoint, FASTLINK_IMPORT_CHECKPOINT_KEY,
  isSeedLikeName,
  buildFastlinkSubmissionContext, buildFastlinkSubmissionMeta,
  createFastlinkScanCheckpoint,
  normalizeSeedFolderId, readTableSelectionRecords
};
`;
const checkpointStore = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakSet, WeakMap,
  RegExp, Intl, Symbol, Error, DOMException, Promise, TextEncoder, TextDecoder, BigInt,
  Uint8Array, ArrayBuffer, structuredClone, setTimeout, clearTimeout, AbortController, fetch,
  localStorage: {
    getItem: (key) => (checkpointStore.has(key) ? checkpointStore.get(key) : null),
    setItem: (key, value) => checkpointStore.set(key, String(value)),
    removeItem: (key) => checkpointStore.delete(key)
  }
};
sandbox.globalThis = sandbox;
sandbox.window = {};
sandbox.document = { querySelectorAll: () => [], getElementById: () => null };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { md5Hex, stringByteSize, hexToBase62, base62ToHex, validEtag, buildFastlinkText, buildFastlinkJson, parseFastlink, generateSecondaryFastlink, saveSecondaryFastlink, saveFastlinkFromCloudFile, resolveAndImportFastlink, resolveAndImportFastlinkInput, resolveAndImportFastlinkSegments, saveFastlinkFromCloudFiles, mergeFastlinkBatchResults, splitFastlinkImportPayloads, fastlinkSegmentPreview, readFastlinkImportCheckpoint, FASTLINK_IMPORT_CHECKPOINT_KEY, isSeedLikeName, buildFastlinkSubmissionContext, buildFastlinkSubmissionMeta, createFastlinkScanCheckpoint, normalizeSeedFolderId, readTableSelectionRecords } = sandbox.__fastlink;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const md5 = (text) => crypto.createHash("md5").update(Buffer.from(text, "utf8")).digest("hex");

// ---------- MD5（上游 v2026.9.2.1「中文输入 md5 计算错误」修复的回归） ----------
const md5Cases = [
  "",
  "hello",
  "abc",
  "中文文件名.json",
  "剧集 第01集.123fastlink.json",
  "天翼\ud83c\udf89emoji 混合",
  JSON.stringify({ commonPath: "剧集/", files: [{ path: "第01集.mp4", etag: "abc", size: 1 }] }),
  "a".repeat(1000000)
];
for (const [index, value] of md5Cases.entries()) {
  test(`md5Hex 用例 ${index} 与 node:crypto 一致（utf8 字节）`, () => {
    assert.equal(md5Hex(value), md5(value));
  });
}
test("stringByteSize 与 Buffer.byteLength 一致", () => {
  assert.equal(stringByteSize("中文abc"), Buffer.byteLength("中文abc", "utf8"));
  assert.equal(stringByteSize(""), 0);
});

// ---------- Base62 往返 ----------
test("hexToBase62/base62ToHex 对 MD5 摘要往返无损", () => {
  for (const value of md5Cases) {
    const hex = md5(value);
    assert.ok(validEtag(hex));
    assert.equal(base62ToHex(hexToBase62(hex)), hex);
  }
});
test("二级链接 etag 使用 Base62（解析后还原为 hex）", () => {
  const hex = md5("种子内容");
  const b62 = hexToBase62(hex);
  assert.notEqual(b62, hex);
  assert.equal(base62ToHex(b62), hex);
});

// ---------- 二级链接构建与解析 ----------
test("单文件二级链接格式为 123FLCPV2$%<b62>#<size>#<名称>", () => {
  const etag = md5("种子内容");
  const link = buildFastlinkText([{ name: "剧名.123fastlink.json", fileName: "剧名.123fastlink.json", etag, size: 123, path: "剧名.123fastlink.json" }]);
  const b62 = hexToBase62(etag);
  assert.equal(link, `123FLCPV2$%${b62}#123#剧名.123fastlink.json`);
  const parsed = parseFastlink(link);
  assert.equal(parsed.files.length, 1);
  assert.equal(parsed.files[0].etag, etag);
  assert.equal(parsed.files[0].size, 123);
  assert.equal(parsed.files[0].fileName, "剧名.123fastlink.json");
});

// ---------- normalizeSeedFolderId ----------
test("种子文件夹 ID：空值放行，非数字报错，任意位数字均放行", () => {
  assert.equal(normalizeSeedFolderId(""), "");
  assert.equal(normalizeSeedFolderId(null), "");
  assert.equal(normalizeSeedFolderId(" 12345678 "), "12345678");
  assert.equal(normalizeSeedFolderId("123"), "123");
  assert.throws(() => normalizeSeedFolderId("1234567a"), /数字 ID/);
  assert.throws(() => normalizeSeedFolderId("abc"), /数字 ID/);
});

// ---------- 全链路：生成二级链接 → 保存二级链接 ----------
function createMockApi(state) {
  return {
    async listAll(parentId) {
      return state.tree[String(parentId)] || [];
    },
    async fileInfos(ids) {
      return ids.map((id) => state.info[String(id)]).filter(Boolean);
    },
    async uploadTextFile(fileName, text, parentId) {
      const record = { id: String(++state.uploadSeq), name: fileName, etag: md5(text), size: Buffer.byteLength(text, "utf8"), parentId: String(parentId), type: 0, content: text };
      state.uploads.push(record);
      return { id: record.id, etag: record.etag, size: record.size, reused: false };
    },
    async reuseFile(file, parentFileId) {
      const key = `${file.etag}:${file.size}:${file.fileName || file.name}`;
      if (!state.cloud[key]) throw new Error("云端没有可复用的同哈希文件");
      return state.cloud[key];
    },
    async readFileText(file) {
      const record = state.filesById[String(file.id)];
      assert.ok(record, `readFileText: 文件 ${file.id} 应已转存`);
      return { name: record.name, text: record.content };
    },
    async ensurePath(rootId, parts) {
      return [rootId, ...parts].join("/");
    }
  };
}

test("生成二级链接：种子上传内容为一级 JSON，链接为单文件 Base62", async () => {
  const episodeEtag = md5("第01集内容");
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: { 0: [{ id: "10", name: "剧集", type: 1 }], 10: [{ id: "11", name: "第01集.mp4", type: 0, size: 5, etag: episodeEtag }] },
    info: {},
    cloud: {},
    filesById: {}
  };
  const api = createMockApi(state);
  const artifact = await generateSecondaryFastlink(api, [{ id: "10", name: "剧集", type: 1 }], { currentDir: "0", seedFolderId: "", useJson: true });
  assert.equal(state.uploads.length, 1);
  const seed = state.uploads[0];
  assert.equal(seed.name, "剧集.123fastlink.json");
  const seedPayload = JSON.parse(seed.content);
  assert.equal(seedPayload.commonPath, "剧集/");
  assert.equal(seedPayload.files[0].fileName, "第01集.mp4");
  const parsed = parseFastlink(artifact.link);
  assert.equal(parsed.files.length, 1, "二级链接应只包含 1 个种子文件");
  assert.equal(parsed.files[0].etag, seed.etag);
  assert.equal(parsed.files[0].size, seed.size);
  assert.equal(artifact.fileCount, 1);
  assert.ok(seed.content.includes(hexToBase62(episodeEtag)), "一级 JSON 应包含原文件 etag（Base62 形式）");
  const oneLevel = parseFastlink(artifact.text);
  assert.equal(oneLevel.files[0].fileName, "第01集.mp4");
});

test("保存二级链接：先秒传种子，再按种子内容完整转存", async () => {
  const episodeEtag = md5("第01集内容");
  const firstLevel = buildFastlinkJson([{ name: "第01集.mp4", etag: episodeEtag, size: 5, path: "剧集/第01集.mp4" }]);
  const seedEtag = md5(firstLevel);
  const seedId = "9001";
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: { [`${seedEtag}:${Buffer.byteLength(firstLevel, "utf8")}:剧集.123fastlink.json`]: seedId },
    filesById: { [seedId]: { name: "剧集.123fastlink.json", content: firstLevel } },
    transferred: []
  };
  const api = createMockApi(state);
  api.reuseFile = async (file, parentFileId) => {
    state.transferred.push({ etag: file.etag, name: file.fileName || file.name, parentId: String(parentFileId) });
    const key = `${file.etag}:${file.size}:${file.fileName || file.name}`;
    if (state.cloud[key]) return state.cloud[key];
    return `t-${state.transferred.length}`;
  };
  const link = `123FLCPV2$%${hexToBase62(seedEtag)}#${Buffer.byteLength(firstLevel, "utf8")}#剧集.123fastlink.json`;
  const result = await saveSecondaryFastlink(api, link, "777", { seedFolderId: "", concurrency: 2 });
  assert.equal(state.transferred[0].name, "剧集.123fastlink.json", "第一步应转存种子文件");
  assert.equal(state.transferred[0].parentId, "777", "种子默认转存到目标目录");
  const episode = state.transferred.find((item) => item.name === "第01集.mp4");
  assert.ok(episode, "应按种子内容转存原文件");
  assert.equal(episode.parentId, "777/剧集");
  assert.equal(result.ok, 1);
  assert.equal(result.fail, 0);
});

test("二级链接包含多个文件时直接拒绝", async () => {
  const api = createMockApi({ cloud: {}, filesById: {}, transferred: [] });
  await assert.rejects(
    saveSecondaryFastlink(api, `123FLCPV2$%${hexToBase62(md5("a"))}#1#a.json$${hexToBase62(md5("b"))}#2#b.json`, "0", {}),
    /只包含 1 个种子文件/
  );
});

test("从秒传文件获取：读取云盘文本并转存", async () => {
  const episodeEtag = md5("第01集内容");
  const firstLevel = buildFastlinkJson([{ name: "第01集.mp4", etag: episodeEtag, size: 5, path: "第01集.mp4" }]);
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {},
    filesById: { "5001": { name: "别人发的.json", content: firstLevel } },
    transferred: []
  };
  const api = createMockApi(state);
  api.reuseFile = async (file, parentFileId) => {
    state.transferred.push({ name: file.fileName || file.name, parentId: String(parentFileId) });
    return `t-${state.transferred.length}`;
  };
  const item = { id: "5001", name: "别人发的.json", type: 0, size: Buffer.byteLength(firstLevel, "utf8"), etag: md5("x"), s3KeyFlag: "flag" };
  const result = await saveFastlinkFromCloudFile(api, item, "42", { concurrency: 2 });
  assert.equal(state.transferred[0].name, "第01集.mp4");
  assert.equal(state.transferred[0].parentId, "42");
  assert.equal(result.ok, 1);
  await assert.rejects(saveFastlinkFromCloudFile(api, { id: "6000", name: "目录", type: 1 }, "42", {}), /而非文件夹/);
});

test("readTableSelectionRecords 在无 React 表格时返回 null", () => {
  assert.equal(readTableSelectionRecords(), null);
});

test("isSeedLikeName 识别种子文件名", () => {
  assert.ok(isSeedLikeName("剧名 (2026) {tmdb-1}.123fastlink.json"));
  assert.ok(isSeedLikeName("链接备份.123fastlink.txt"));
  assert.ok(!isSeedLikeName("普通视频.mkv"));
  assert.ok(!isSeedLikeName("普通.json"));
});

test("自动识别：单文件种子名走二级还原，普通内容直接转存", async () => {
  const episodeEtag = md5("第01集内容");
  const firstLevel = buildFastlinkJson([{ name: "第01集.mp4", etag: episodeEtag, size: 5, path: "第01集.mp4" }]);
  const seedEtag = md5(firstLevel);
  const seedId = "9001";
  const calls = { seedResolved: 0, transferred: [] };
  const api = createMockApi({
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: { [`${seedEtag}:${Buffer.byteLength(firstLevel, "utf8")}:剧名.123fastlink.json`]: seedId },
    filesById: { [seedId]: { name: "剧名.123fastlink.json", content: firstLevel } },
    transferred: []
  });
  api.reuseFile = async (file, parentFileId) => {
    calls.transferred.push(file.fileName || file.name);
    const key = `${file.etag}:${file.size}:${file.fileName || file.name}`;
    if (api.__cloud && api.__cloud[key]) return api.__cloud[key];
    if (calls.transferred.length === 1) {
      calls.seedResolved += 1;
      return seedId;
    }
    return `t-${calls.transferred.length}`;
  };
  api.readFileText = async (file) => ({ name: file.name, text: firstLevel });
  const secondaryLink = `123FLCPV2$%${hexToBase62(seedEtag)}#${Buffer.byteLength(firstLevel, "utf8")}#剧名.123fastlink.json`;
  const result = await resolveAndImportFastlink(api, secondaryLink, "7", { concurrency: 2 });
  assert.equal(calls.seedResolved, 1, "单文件种子名应先还原种子");
  assert.equal(result.ok, 1);

  // 普通单文件链接（名字不是种子）不走二级还原，直接转存该文件
  calls.transferred.length = 0;
  const plainLink = `123FLCPV2$%${hexToBase62(md5("电影本体"))}#123#Movie.2026.1080p.mkv`;
  await resolveAndImportFastlink(api, plainLink, "7", { concurrency: 2 });
  assert.deepEqual(calls.transferred, ["Movie.2026.1080p.mkv"]);
});

// ---------- 秒传直投识别上下文（buildFastlinkSubmissionContext） ----------
test("直投上下文：标准 JSON 抽真实文件名与 totalSize", () => {
  const json = JSON.stringify({
    commonPath: "J Music (2023) {tmdb-272272}/",
    totalFilesCount: 2,
    totalSize: 6547244819,
    usesBase62EtagsInExport: true,
    files: [
      { path: "Season 1/J Music.2023.S01E01.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv", fileName: "J Music.2023.S01E01.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv", etag: md5("e1"), size: 3666910245 },
      { path: "Season 1/J Music.2023.S01E02.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv", fileName: "J Music.2023.S01E02.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv", etag: md5("e2"), size: 2880334574 }
    ]
  });
  const context = buildFastlinkSubmissionContext("J Music (2023) {tmdb-272272}", json);
  const lines = context.split("\n");
  assert.equal(lines[0], "🎬：J Music (2023) {tmdb-272272}");
  assert.match(lines[1], /^💾：6\.\d+ GB$/);
  assert.ok(lines.includes("📄：Season 1/J Music.2023.S01E01.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv"));
  assert.ok(lines.includes("📄：Season 1/J Music.2023.S01E02.1080p.MyTVSuper.WEB-DL.H26.AAC-ADWeb.mkv"));
  assert.equal(lines.length, 4, "path 与 fileName 不应重复出现");
});

test("直投上下文：V2 链接文本按 $/# 抽路径与体积", () => {
  const text = buildFastlinkText([
    { name: "剧名", etag: md5("e1"), size: 100, path: "Season 1/第01集.mkv" },
    { name: "剧名", etag: md5("e2"), size: 250, path: "Season 1/第02集.mkv" }
  ]);
  const context = buildFastlinkSubmissionContext("剧名 (2026)", text);
  const lines = context.split("\n");
  assert.equal(lines[0], "🎬：剧名 (2026)");
  assert.match(lines[1], /^💾：350 B$/);
  // buildFastlinkText 会把公共前缀提为 commonPath，条目里只剩文件名
  assert.ok(lines.includes("📄：第01集.mkv"));
  assert.ok(lines.includes("📄：第02集.mkv"));
});

test("直投上下文：文件名封顶 1000 条并补「还有 N 个文件」，130 集完整带出；空内容返回空串", () => {
  const files = Array.from({ length: 130 }, (_, index) => ({ name: `f${index}`, etag: md5(`e${index}`), size: 1, path: `第${index}集.mkv` }));
  const context = buildFastlinkSubmissionContext("剧名", buildFastlinkText(files));
  const nameLines = context.split("\n").filter((line) => line.startsWith("📄："));
  assert.equal(nameLines.length, 130, "130 集不再被 100 条上限截断");
  assert.ok(!context.includes("还有"), "未超上限不应出现补行");

  const many = Array.from({ length: 1030 }, (_, index) => ({ name: `f${index}`, etag: md5(`e${index}`), size: 1, path: `第${index}集.mkv` }));
  const manyContext = buildFastlinkSubmissionContext("剧名", buildFastlinkText(many));
  const manyLines = manyContext.split("\n");
  assert.equal(manyLines.filter((line) => line.startsWith("📄：") && !line.includes("还有")).length, 1000, "清单封顶 1000 条");
  assert.equal(manyLines.at(-1), "📄：还有 30 个文件", "截断时补一行剩余数量");

  const bigJson = JSON.stringify({ commonPath: "", files: Array.from({ length: 1050 }, (_, index) => ({ path: `p${index}.mkv`, etag: md5(`e${index}`), size: 1 })) });
  assert.ok(buildFastlinkSubmissionContext("剧名", bigJson).endsWith("📄：还有 50 个文件"), "JSON 形态同样补行");

  assert.equal(buildFastlinkSubmissionContext("剧名", ""), "");
  // 只有种子记录、没有路径字段时也返回空串（回退旧文案，不带误导上下文）
  assert.equal(buildFastlinkSubmissionContext("剧名", "123FLCPV2$%abc#1#"), "");
});

// ---------- 直投结构化 meta（全量扫描算季集范围） ----------
test("直投 meta：486 集裸集号按目录推季号算全量范围，📄 截断不影响", () => {
  const files = Array.from({ length: 486 }, (_, index) => ({
    path: `万界独尊 2021 100集(4K)/第1季/2021.E${index + 1}.mkv`,
    fileName: `2021.E${index + 1}.mkv`,
    etag: md5(`e${index}`),
    size: 10
  }));
  const meta = buildFastlinkSubmissionMeta(buildFastlinkJson(files));
  assert.equal(meta.seasonEpisode, "S01E01-E486");
  assert.equal(meta.mediaType, "tv");
  // 📄 上下文清单截断到 1000 条，meta 仍按全量算
  const big = Array.from({ length: 1100 }, (_, index) => ({
    path: `One.Piece/Season 1/E${index + 1}.mkv`,
    fileName: `E${index + 1}.mkv`,
    etag: md5(`b${index}`),
    size: 10
  }));
  const bigMeta = buildFastlinkSubmissionMeta(buildFastlinkJson(big));
  assert.equal(bigMeta.seasonEpisode, "S01E01-E1100");
  assert.ok(buildFastlinkSubmissionContext("海贼王", buildFastlinkText(big)).split("\n").filter((line) => line.startsWith("📄：第") || line.startsWith("📄：E")).length < 1100, "文件名清单确实截断");
});

test("直投 meta：多季按季号区间、单文件范围号并入、电影也带技术字段", () => {
  const multi = [
    { path: "番/第1季/E1.mkv", fileName: "E1.mkv", etag: md5("a1"), size: 1 },
    { path: "番/第1季/E2.mkv", fileName: "E2.mkv", etag: md5("a2"), size: 1 },
    { path: "番/第2季/E1.mkv", fileName: "E1.mkv", etag: md5("b1"), size: 1 },
    { path: "番/第3季/E5.mkv", fileName: "E5.mkv", etag: md5("c1"), size: 1 }
  ];
  assert.equal(buildFastlinkSubmissionMeta(buildFastlinkText(multi)).seasonEpisode, "S01-S03");
  const ranged = [{ path: "Show/Season 1/Show.S01E01-E05.mkv", fileName: "Show.S01E01-E05.mkv", etag: md5("r1"), size: 1 }];
  assert.equal(buildFastlinkSubmissionMeta(buildFastlinkText(ranged)).seasonEpisode, "S01E01-E05");
  // 电影没有集号：不带 seasonEpisode/mediaType（类型仍由客户端判断），但技术字段以脚本识别为准
  const movie = [{ path: "Movie.2019.2160p.WEB-DL.x265.EAC3.5.1.mkv", fileName: "Movie.2019.2160p.WEB-DL.x265.EAC3.5.1.mkv", etag: md5("m1"), size: 1 }];
  const movieMeta = buildFastlinkSubmissionMeta(buildFastlinkText(movie));
  assert.equal(movieMeta.seasonEpisode, undefined);
  assert.equal(movieMeta.mediaType, undefined);
  assert.equal(movieMeta.quality, "2160p");
  assert.equal(movieMeta.source, "WEB-DL");
  assert.equal(movieMeta.videoCodec, "HEVC", "编码归并到客户端词表（x265→HEVC）");
  assert.equal(buildFastlinkSubmissionMeta(""), null);
});

test("直投 meta：同一编码多种写法归并一种，音轨数后缀不进备注", () => {
  const files = [
    { path: "S1/Show.S01E01.2160p.WEB-DL.x265.DDP.5.1.2Audios.mkv", fileName: "Show.S01E01.2160p.WEB-DL.x265.DDP.5.1.2Audios.mkv", etag: md5("c1"), size: 1 },
    { path: "S1/Show.S01E02.2160p.WEB-DL.HEVC.DDP.5.1.mkv", fileName: "Show.S01E02.2160p.WEB-DL.HEVC.DDP.5.1.mkv", etag: md5("c2"), size: 1 },
    { path: "S1/Show.S01E03.2160p.WEB-DL.H265.DDP.5.1.3Audios.mkv", fileName: "Show.S01E03.2160p.WEB-DL.H265.DDP.5.1.3Audios.mkv", etag: md5("c3"), size: 1 }
  ];
  const meta = buildFastlinkSubmissionMeta(buildFastlinkText(files));
  assert.equal(meta.videoCodec, "HEVC", "x265/HEVC/H265 是同一种编码，备注只写一种");
  assert.equal(meta.audioCodec, "DDP.5.1", "2Audios/3Audios 只是音轨数，归并后不重复罗列");
  assert.equal(meta.seasonEpisode, "S01E01-E03");
});

test("直投 meta：多版本变体用 / 汇总、自定义映射表生效", () => {
  const mixed = [
    { path: "S1/Show.S01E01.2160p.WEB-DL.x265.mkv", fileName: "Show.S01E01.2160p.WEB-DL.x265.mkv", etag: md5("v1"), size: 1 },
    { path: "S1/Show.S01E01.1080p.WEB-DL.x264.mkv", fileName: "Show.S01E01.1080p.WEB-DL.x264.mkv", etag: md5("v2"), size: 1 }
  ];
  const meta = buildFastlinkSubmissionMeta(buildFastlinkText(mixed));
  assert.equal(meta.quality.split("/").sort().join(","), "1080p,2160p", "画质变体用 / 汇总（与客户端 join_variants 同格式）");
  assert.equal(meta.videoCodec.split("/").sort().join(","), "AVC,HEVC");
  // 自定义映射：把 WEB-DL 改写成 WEB 的映射表要影响输出
  const custom = [{ id: "custom-web", field: "resourceType", aliases: ["WEB-DL"], output: "WEB" }];
  const mapped = buildFastlinkSubmissionMeta(buildFastlinkText(mixed), custom);
  assert.ok(!String(mapped.source).includes("WEB-DL"), "自定义映射表参与直投识别");
});

test("直投 meta 接线：push 按自定义映射计算并随条目提交，submitShares 只在有条目对象时带上", () => {
  const bundle = lines.join("\n");
  assert.ok(bundle.includes("const meta = buildFastlinkSubmissionMeta(artifact?.text, (this.config.library || {}).recognition?.fixedMappings);"), "直投要计算 meta（带自定义映射表）");
  assert.ok(bundle.includes("...(item.meta && typeof item.meta === \"object\" ? { meta: item.meta } : {})"), "submitShares 要透传 meta 且不误导老后端");
});

test("二级链接成功要清扫描断点，上传种子失败保留断点可续扫", async () => {
  const folders = new Map([
    ["f1", [
      { id: "v1", name: "Show.S01E01.1080p.WEB-DL.mkv", type: 0, size: 100, etag: md5("etag") },
      { id: "v2", name: "Show.S01E02.1080p.WEB-DL.mkv", type: 0, size: 200, etag: md5("etag") }
    ]]
  ]);
  const apiFor = (failUpload) => ({
    async listAll(id) { return folders.get(String(id)) || []; },
    async fileInfos() { return []; },
    async uploadTextFile(fileName, content, parentId) {
      if (failUpload) throw new Error("上传失败");
      return { id: "seed1", etag: md5("etag"), size: content.length };
    }
  });
  const items = [{ id: "f1", name: "剧集", type: 1 }];
  const okCheckpoint = createFastlinkScanCheckpoint(items, {});
  await generateSecondaryFastlink(apiFor(false), items, { useJson: true, seedFolderId: "9", checkpoint: okCheckpoint });
  assert.equal(okCheckpoint.state, null, "全链成功后断点必须清掉，否则面板继续提示「上次扫描进度」");
  const failCheckpoint = createFastlinkScanCheckpoint(items, {});
  await assert.rejects(() => generateSecondaryFastlink(apiFor(true), items, { useJson: true, seedFolderId: "9", checkpoint: failCheckpoint }));
  assert.ok(failCheckpoint.state, "种子上传失败要保留断点供续扫");
});

// ---------- 多条秒传一次导入（1.4.4） ----------
const seedLinkFor = (name, content) => `123FLCPV2$%${hexToBase62(md5(content))}#${Buffer.byteLength(content, "utf8")}#${name}`;
const episodeJsonFor = (title, fileName, etagSeed) => buildFastlinkJson([{ name: fileName, etag: md5(etagSeed), size: 5, path: `${title}/${fileName}` }]);

test("切分：多条二级链接一行一条切成多段，每段可独立解析", () => {
  const c1 = episodeJsonFor("剧A", "A01.mp4", "a1");
  const c2 = episodeJsonFor("剧B", "B01.mp4", "b1");
  const c3 = episodeJsonFor("剧C", "C01.mp4", "c1");
  const l1 = seedLinkFor("剧A.123fastlink.json", c1);
  const l2 = seedLinkFor("剧B.123fastlink.json", c2);
  const l3 = seedLinkFor("剧C.123fastlink.json", c3);
  const segments = splitFastlinkImportPayloads(`${l1}\n${l2}\n${l3}`);
  assert.equal(segments.length, 3);
  assert.equal(parseFastlink(segments[0]).files[0].fileName, "剧A.123fastlink.json");
  assert.equal(parseFastlink(segments[1]).files[0].fileName, "剧B.123fastlink.json");
  assert.equal(parseFastlink(segments[2]).files[0].fileName, "剧C.123fastlink.json");
});

test("切分：单条内容内部换行分条不会被切开（旧语义保留）", () => {
  const built = buildFastlinkText([
    { name: "第01集.mp4", etag: md5("e1"), size: 5, path: "剧/第01集.mp4" },
    { name: "第02集.mp4", etag: md5("e2"), size: 6, path: "剧/第02集.mp4" }
  ]);
  // 记录分隔符 $ 换成换行：一条链接内部本来就允许换行分条
  const separator = built.indexOf("%");
  const multiline = built.slice(0, separator + 1) + built.slice(separator + 1).split("$").join("\n");
  const segments = splitFastlinkImportPayloads(multiline);
  assert.equal(segments.length, 1, "单条多记录内容应保持一段");
  assert.equal(parseFastlink(segments[0]).files.length, 2, "换行分条记录照常解析");
  assert.equal(segments[0], multiline.trim());
});

test("切分：JSON 对象跨行聚合成段，两个 JSON 切成两段，截断 JSON 归一段", () => {
  const j1 = JSON.stringify({ name: "剧A", commonPath: "剧A/", files: [{ path: "1.mp4", etag: md5("a1"), size: 1 }] }, null, 2);
  const j2 = JSON.stringify({ name: "剧B", commonPath: "剧B/", files: [{ path: "2.mp4", etag: md5("b1"), size: 1 }] }, null, 2);
  const segments = splitFastlinkImportPayloads(`${j1}\n${j2}`);
  assert.equal(segments.length, 2);
  assert.equal(JSON.parse(segments[0]).name, "剧A");
  assert.equal(JSON.parse(segments[1]).name, "剧B");
  const truncated = splitFastlinkImportPayloads(j1.slice(0, 40));
  assert.equal(truncated.length, 1, "截断 JSON 不抛错，整段交由解析层报原文错误");
  const mixed = splitFastlinkImportPayloads(`${j1}\n\n${seedLinkFor("剧B.123fastlink.json", j2)}`);
  assert.equal(mixed.length, 2, "JSON 与二级链接混排各成一段，空行跳过");
});

test("切分：行首空白的前缀也断段；纯文本/空输入返回单段或空数组", () => {
  const l1 = seedLinkFor("剧A.123fastlink.json", episodeJsonFor("剧A", "A01.mp4", "a1"));
  const l2 = seedLinkFor("剧B.123fastlink.json", episodeJsonFor("剧B", "B01.mp4", "b1"));
  const segments = splitFastlinkImportPayloads(`  ${l1}\n\n\t${l2}`);
  assert.equal(segments.length, 2, "行首空白缩进的前缀同样断段");
  assert.equal(splitFastlinkImportPayloads("随便什么文本\n第二行").length, 1);
  // 沙箱里创建的数组原型与宿主不同，deepEqual 会误判，按长度比
  assert.equal(splitFastlinkImportPayloads("   ").length, 0, "空白输入返回空数组（入口照旧报「请粘贴秒传内容」）");
});

test("fastlinkSegmentPreview：二级链接取种子名，JSON 取 name 字段，纯文本取首行", () => {
  assert.equal(fastlinkSegmentPreview(seedLinkFor("剧A.123fastlink.json", "{}")), "剧A.123fastlink.json");
  assert.equal(fastlinkSegmentPreview(JSON.stringify({ name: "剧B", files: [] })), "剧B");
  assert.equal(fastlinkSegmentPreview("第一行内容\n第二行"), "第一行内容");
});

test("批量导入端到端：两条二级链接逐条转存并汇总，进度回调带段序", async () => {
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {
      [`${md5(contentA)}:${Buffer.byteLength(contentA, "utf8")}:剧A.123fastlink.json`]: "9001",
      [`${md5(contentB)}:${Buffer.byteLength(contentB, "utf8")}:剧B.123fastlink.json`]: "9002"
    },
    filesById: { 9001: { name: "剧A.123fastlink.json", content: contentA }, 9002: { name: "剧B.123fastlink.json", content: contentB } },
    transferred: []
  };
  const api = createMockApi(state);
  api.reuseFile = async (file, parentFileId) => {
    const name = file.fileName || file.name;
    state.transferred.push({ name, parentId: String(parentFileId) });
    const key = `${file.etag}:${file.size}:${name}`;
    if (state.cloud[key]) return state.cloud[key];
    return `t-${state.transferred.length}`;
  };
  const events = [];
  const input = `${seedLinkFor("剧A.123fastlink.json", contentA)}\n${seedLinkFor("剧B.123fastlink.json", contentB)}`;
  const result = await resolveAndImportFastlinkInput(api, { text: input }, "7", {
    concurrency: 2,
    onSegmentStart: (index, count, name) => events.push([index, count, name])
  });
  assert.deepEqual(events, [[1, 2, "剧A.123fastlink.json"], [2, 2, "剧B.123fastlink.json"]], "每段开始回调带段序与名字");
  assert.equal(result.ok, 2, "两条链接各导入 1 个文件");
  assert.equal(result.fail, 0);
  assert.equal(result.links.length, 2);
  assert.ok(!result.links[0].error && !result.links[1].error);
  assert.ok(state.transferred.some((item) => item.name === "剧A.123fastlink.json"), "第一条先转存种子");
  assert.ok(state.transferred.some((item) => item.name === "B01.mp4"), "第二条内容照常导入");
  assert.equal(result.status, "success");
});

test("批量导入：某条失败跳过继续其余，汇总为部分完成并带失败原因", async () => {
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    // 种子 A 不在云端：转存种子这步直接失败
    cloud: { [`${md5(contentB)}:${Buffer.byteLength(contentB, "utf8")}:剧B.123fastlink.json`]: "9002" },
    filesById: { 9002: { name: "剧B.123fastlink.json", content: contentB } },
    transferred: []
  };
  const api = createMockApi(state);
  api.reuseFile = async (file, parentFileId) => {
    const name = file.fileName || file.name;
    const key = `${file.etag}:${file.size}:${name}`;
    if (state.cloud[key]) {
      state.transferred.push({ name, parentId: String(parentFileId) });
      return state.cloud[key];
    }
    // 只有「种子不在云端」才算整条失败；普通内容文件按新转存落地（与端到端用例同一套 mock 语义）
    if (isSeedLikeName(name)) throw new Error("云端没有可复用的同哈希文件");
    state.transferred.push({ name, parentId: String(parentFileId) });
    return `t-${state.transferred.length}`;
  };
  const input = `${seedLinkFor("剧A.123fastlink.json", contentA)}\n${seedLinkFor("剧B.123fastlink.json", contentB)}`;
  const result = await resolveAndImportFastlinkInput(api, { text: input }, "7", { concurrency: 2 });
  assert.equal(result.status, "partial");
  assert.equal(result.ok, 1, "第二条照常导入");
  assert.equal(result.fail, 1, "失败的那条按 1 条失败计入");
  assert.match(result.links[0].error, /云端没有/);
  assert.ok(!result.links[1].error);
  const failedRow = result.details.find((item) => item.status === "failed");
  assert.ok(failedRow && failedRow.name.includes("第 1 条"), "失败链接以伪明细行进结果页");
  assert.ok(state.transferred.some((item) => item.name === "B01.mp4"), "后续条目不受前一条失败影响");
});

test("批量导入：用户中止（AbortError）立即上抛，不再继续后面的段", async () => {
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const abortError = new Error("用户取消");
  abortError.name = "AbortError";
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {
      [`${md5(contentA)}:${Buffer.byteLength(contentA, "utf8")}:剧A.123fastlink.json`]: "9001",
      [`${md5(contentB)}:${Buffer.byteLength(contentB, "utf8")}:剧B.123fastlink.json`]: "9002"
    },
    filesById: {},
    reads: []
  };
  const api = createMockApi(state);
  api.readFileText = async (file) => {
    state.reads.push(String(file.id));
    throw abortError;
  };
  await assert.rejects(
    resolveAndImportFastlinkSegments(api, [seedLinkFor("剧A.123fastlink.json", contentA), seedLinkFor("剧B.123fastlink.json", contentB)], "7", { concurrency: 2 }),
    (error) => error.name === "AbortError"
  );
  assert.equal(state.reads.length, 1, "第一条中止后不再读第二条");
});

test("批量导入断点：各段各存各的键，全成功的段清档、未完成的段保留", async () => {
  checkpointStore.clear();
  const missError = new Error("秒传未命中");
  missError.fastlinkMiss = true;
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {
      [`${md5(contentA)}:${Buffer.byteLength(contentA, "utf8")}:剧A.123fastlink.json`]: "9001",
      [`${md5(contentB)}:${Buffer.byteLength(contentB, "utf8")}:剧B.123fastlink.json`]: "9002"
    },
    filesById: { 9001: { name: "剧A.123fastlink.json", content: contentA }, 9002: { name: "剧B.123fastlink.json", content: contentB } },
    missNames: ["A01.mp4"]
  };
  const api = createMockApi(state);
  api.reuseFile = async (file) => {
    const name = file.fileName || file.name;
    if (state.missNames.includes(name)) throw missError;
    const key = `${file.etag}:${file.size}:${name}`;
    if (state.cloud[key]) return state.cloud[key];
    return `t-${name}`;
  };
  const input = `${seedLinkFor("剧A.123fastlink.json", contentA)}\n${seedLinkFor("剧B.123fastlink.json", contentB)}`;
  const result = await resolveAndImportFastlinkInput(api, { text: input }, "7", { concurrency: 2, importProgress: true });
  assert.equal(result.miss, 1, "A 的正片未命中单列，不算失败");
  assert.equal(result.ok, 1, "B 照常成功");
  const seg0Key = `${FASTLINK_IMPORT_CHECKPOINT_KEY}#seg0`;
  const seg1Key = `${FASTLINK_IMPORT_CHECKPOINT_KEY}#seg1`;
  assert.ok(checkpointStore.has(seg0Key), "未完成段（A）的断点保留在自己的段键上");
  assert.ok(!checkpointStore.has(seg1Key), "全部成功段（B）的段键已清");
  assert.ok(!checkpointStore.has(String(FASTLINK_IMPORT_CHECKPOINT_KEY)), "批量导入不碰单条导入的主断点键");
  const seg0 = readFastlinkImportCheckpoint("#seg0");
  assert.ok(seg0 && seg0.contentHash, "段断点可按后缀读回");
});

test("云盘多文件：勾选两个秒传文件逐个转存汇总；一个失败继续另一个", async () => {
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const buildState = (failSecond) => ({
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {},
    filesById: { 5001: { name: "剧A.json", content: contentA }, 5002: { name: "剧B.json", content: contentB } },
    transferred: [],
    failSecond
  });
  const makeApi = (state) => {
    const api = createMockApi(state);
    api.readFileText = async (file) => {
      if (state.failSecond && String(file.id) === "5002") throw new Error("读取失败");
      const record = state.filesById[String(file.id)];
      assert.ok(record, `readFileText: 文件 ${file.id} 应存在`);
      return { name: record.name, text: record.content };
    };
    api.reuseFile = async (file, parentFileId) => {
      state.transferred.push({ name: file.fileName || file.name, parentId: String(parentFileId) });
      return `t-${state.transferred.length}`;
    };
    return api;
  };
  const okResult = await saveFastlinkFromCloudFiles(makeApi(buildState(false)), [
    { id: "5001", name: "剧A.json", type: 0 },
    { id: "5002", name: "剧B.json", type: 0 }
  ], "42", { concurrency: 2 });
  assert.equal(okResult.ok, 2);
  assert.equal(okResult.links.length, 2);
  assert.ok(!okResult.links[0].error && !okResult.links[1].error);

  const partialResult = await saveFastlinkFromCloudFiles(makeApi(buildState(true)), [
    { id: "5001", name: "剧A.json", type: 0 },
    { id: "5002", name: "剧B.json", type: 0 }
  ], "42", { concurrency: 2 });
  assert.equal(partialResult.status, "partial");
  assert.equal(partialResult.ok, 1, "成功的那个照常导入");
  assert.match(partialResult.links[1].error, /读取失败/);
  // 单文件路径保持原语义：失败直接上抛
  await assert.rejects(
    saveFastlinkFromCloudFiles(makeApi(buildState(true)), [{ id: "5002", name: "剧B.json", type: 0 }], "42", { concurrency: 2 }),
    /读取失败/
  );
});

test("云盘多文件断点：每个文件各存各的段键，没转完的保留、全转完的清档", async () => {
  checkpointStore.clear();
  const missError = new Error("秒传未命中");
  missError.fastlinkMiss = true;
  const contentA = episodeJsonFor("剧A", "A01.mp4", "a1");
  const contentB = episodeJsonFor("剧B", "B01.mp4", "b1");
  const state = {
    uploadSeq: 0,
    uploads: [],
    tree: {},
    info: {},
    cloud: {},
    filesById: { 5001: { name: "剧A.json", content: contentA }, 5002: { name: "剧B.json", content: contentB } },
    missNames: ["A01.mp4"]
  };
  const api = createMockApi(state);
  api.reuseFile = async (file) => {
    const name = file.fileName || file.name;
    if (state.missNames.includes(name)) throw missError;
    return `t-${name}`;
  };
  const result = await saveFastlinkFromCloudFiles(api, [
    { id: "5001", name: "剧A.json", type: 0 },
    { id: "5002", name: "剧B.json", type: 0 }
  ], "42", { concurrency: 2, importProgress: true });
  assert.equal(result.miss, 1, "第一个文件里的未命中单列一桶");
  assert.equal(result.ok, 1, "第二个文件照常转完");
  const seg0Key = `${FASTLINK_IMPORT_CHECKPOINT_KEY}#seg0`;
  const seg1Key = `${FASTLINK_IMPORT_CHECKPOINT_KEY}#seg1`;
  assert.ok(checkpointStore.has(seg0Key), "没转完的第一个文件断点留在自己的段键上（重跑不重转已成功条目）");
  assert.ok(!checkpointStore.has(seg1Key), "全转完的第二个文件段键已清");
  assert.ok(!checkpointStore.has(String(FASTLINK_IMPORT_CHECKPOINT_KEY)), "批量转存不碰单条导入的主断点键");
  checkpointStore.clear();
});

test("mergeFastlinkBatchResults：计数累加、失败链接补伪明细、状态判定", () => {
  const merged = mergeFastlinkBatchResults([
    { ok: 2, fail: 0, miss: 1, skipped: 1, invalid: 1, sanitized: 2, invalidReasons: { path: 1, etag: 0, size: 0, hidden: 0 }, invalidSamples: [{ index: 1 }], details: [{ name: "a", status: "success" }, { name: "b", status: "miss" }], affectedDirIds: ["7", "8"] },
    null,
    { ok: 1, fail: 0, miss: 0, skipped: 0, invalid: 0, sanitized: 0, invalidReasons: { path: 0, etag: 0, size: 0, hidden: 0 }, invalidSamples: [], details: [{ name: "c", status: "success" }], affectedDirIds: ["8"] }
  ], [
    { index: 1, label: "第 1 条", name: "x", ok: 3, fail: 0, miss: 1, error: "" },
    { index: 2, label: "第 2 条", name: "y", ok: 0, fail: 0, miss: 0, error: "坏链接" }
  ]);
  assert.equal(merged.ok, 3);
  assert.equal(merged.miss, 1);
  assert.equal(merged.skipped, 1);
  assert.equal(merged.invalid, 1);
  assert.equal(merged.sanitized, 2);
  assert.equal(merged.invalidReasons.path, 1);
  assert.equal(merged.fail, 1, "失败链接按 1 条失败计入");
  assert.equal(merged.details.length, 4, "明细拼接 + 失败伪明细");
  assert.equal(merged.status, "partial");
  // 沙箱数组原型与宿主不同，deepEqual 会误判，按拼接串比
  assert.equal(merged.affectedDirIds.slice().sort().join(","), "7,8", "受影响目录去重并集");
  assert.equal(merged.links.length, 2);
});

test("多条导入源码契约：控制器与工具栏接线到位", () => {
  const bundle = lines.join("\n");
  assert.ok(bundle.includes("const segments = splitFastlinkImportPayloads(input?.text);"), "粘贴导入要先切分再决定单段/多段");
  assert.ok(bundle.includes("resolveAndImportFastlinkSegments(api, segments, rootId, options)"), "多段走批量执行层");
  assert.ok(!bundle.includes("items.length !== 1"), "云盘转存入口不再硬性只允许 1 个文件");
  assert.ok(bundle.includes("saveFastlinkFromCloudFiles(this.api, itemList"), "云盘转存走多文件批量函数");
  assert.ok(bundle.includes("records.length === this.selection.selectedIds.size"), "工具栏与命令守卫按多选校验全部是种子");
  assert.ok(bundle.includes("fastlinkLinksTable(result2)"), "结果页渲染逐条摘要块");
  assert.ok(bundle.includes("saveFastlinkFromCloudFile(api, item, rootId, { ...options, checkpointKeySuffix: fastlinkSegmentCheckpointSuffix(index) });"), "云盘多文件逐个转存各用各的段断点键");
  assert.ok(bundle.includes("sweepStaleFastlinkSegmentCheckpoints(list.length)"), "批量开跑前回收残留的段断点");
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`  ok ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}`);
    console.log(String(error?.stack || error).split("\n").slice(0, 4).map((line) => `      ${line}`).join("\n"));
  }
}
console.log(failed === 0 ? `\n全部 ${cases.length} 个用例通过` : `\n${failed}/${cases.length} 个用例失败`);
process.exit(failed === 0 ? 0 : 1);
