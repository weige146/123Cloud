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
  slice("// src/core/table-selection.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__fastlink = {
  md5Hex, stringByteSize, hexToBase62, base62ToHex, validEtag,
  buildFastlinkText, buildFastlinkJson, parseFastlink,
  generateSecondaryFastlink, saveSecondaryFastlink, saveFastlinkFromCloudFile,
  normalizeSeedFolderId, readTableSelectionRecords
};
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakSet, WeakMap,
  RegExp, Intl, Symbol, Error, DOMException, Promise, TextEncoder, TextDecoder, BigInt,
  Uint8Array, ArrayBuffer, structuredClone, setTimeout, clearTimeout, AbortController, fetch
};
sandbox.globalThis = sandbox;
sandbox.window = {};
sandbox.document = { querySelectorAll: () => [], getElementById: () => null };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { md5Hex, stringByteSize, hexToBase62, base62ToHex, validEtag, buildFastlinkText, buildFastlinkJson, parseFastlink, generateSecondaryFastlink, saveSecondaryFastlink, saveFastlinkFromCloudFile, normalizeSeedFolderId, readTableSelectionRecords } = sandbox.__fastlink;

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
test("种子文件夹 ID：空值放行，非 8 位数字报错", () => {
  assert.equal(normalizeSeedFolderId(""), "");
  assert.equal(normalizeSeedFolderId(null), "");
  assert.equal(normalizeSeedFolderId(" 12345678 "), "12345678");
  assert.throws(() => normalizeSeedFolderId("123"), /8 位数字/);
  assert.throws(() => normalizeSeedFolderId("1234567a"), /8 位数字/);
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
