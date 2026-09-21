// 秒传导入「自动清理 + 无效跳过 + 千万级流式批次」回归测试。
// 背景：115 等导出的超大秒传 JSON（实测 2.45GB / 1400 万条）里大量路径含连续空格、
// 特殊字符、点开头隐藏文件（.stfolder/.aria2 等），旧逻辑任何一条不合法就整包中止，
// 且全量条目驻留内存。1.3.8 起：导入路径自动清理（连续空格折叠、非法字符删除）、
// 点开头隐藏文件整条跳过、坏 Etag/大小跳过并分类计数；本地大 JSON 走批次导入
// （解析器攒满一批交给 createFastlinkStreamImporter 消费，文件流天然背压）；
// 断点 v3 用 64 位指纹 done 集（BigInt64Array），不再存全量 key 字符串。
// 核心断言：导入语义下流式与文本路径结果严格一致；严格模式（拆分/转换）行为不变。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = dirname2();
function dirname2() {
  return new URL("..", import.meta.url).pathname;
}

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const lines = readFileSync(scriptPath, "utf8").split("\n");
const slice = (fromMarker, toMarker) => {
  const start = lines.findIndex((line) => line.includes(fromMarker));
  const end = lines.findIndex((line) => line.includes(toMarker));
  if (start < 0 || end <= start) throw new Error(`bundle markers not found: ${fromMarker} .. ${toMarker}`);
  return lines.slice(start, end).join("\n");
};
const store = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakMap, RegExp, Intl, Symbol, Error, TypeError,
  DOMException, BigInt, Boolean, Promise, Float64Array,
  TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  Blob: globalThis.Blob, MessageChannel: globalThis.MessageChannel,
  location: { origin: "https://www.123865.com" },
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key)
  }
};
vm.createContext(sandbox);
vm.runInContext([
  slice("// src/core/utils.js", "// src/api.js"),
  slice("// src/api.js", "// src/core/categories.js"),
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js")
].join("\n"), sandbox, { filename: "123-helper.user.js" });
vm.runInContext(`
globalThis.__fastlinkSkip = {
  parseFastlink,
  parseFastlinkJson,
  createFastlinkJsonScanner,
  parseFastlinkInputFile,
  createFastlinkStreamImporter,
  createFastlinkInvalidCollector,
  createFastlinkDoneSet,
  fastlinkKeyFingerprint,
  resolveImportedPathParts,
  resolveAndImportFastlinkInput,
  importFastlink,
  FASTLINK_IMPORT_STREAM_CHECKPOINT_KEY
};
`, sandbox, { filename: "driver.js" });
const {
  parseFastlinkJson,
  createFastlinkJsonScanner,
  parseFastlinkInputFile,
  createFastlinkStreamImporter,
  createFastlinkInvalidCollector,
  createFastlinkDoneSet,
  fastlinkKeyFingerprint,
  resolveImportedPathParts,
  resolveAndImportFastlinkInput,
  importFastlink,
  FASTLINK_IMPORT_STREAM_CHECKPOINT_KEY
} = sandbox.__fastlinkSkip;

const HEX = "0123456789abcdef";
const hexEtag = (seedText) => {
  let out = "";
  for (let index = 0; index < 32; index += 1) out += HEX[(seedText.charCodeAt(index % seedText.length) + index * 7) % 16];
  return out;
};
function makeRng(seed) {
  let state = seed;
  return () => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
}
function streamParse(text, scanner, { maxChunk = 7, seed = 42 } = {}) {
  const rng = makeRng(seed);
  for (let offset = 0; offset < text.length;) {
    const size = Math.min(text.length - offset, 1 + Math.floor(rng() * maxChunk));
    scanner.push(text.slice(offset, offset + size));
    offset += size;
  }
  return scanner.finish();
}

const sameJson = (left, right) => assert.equal(JSON.stringify(left), JSON.stringify(right));

// —— 混合内容：正常 / 连续空格 / 特殊字符 / 点开头隐藏文件 / 坏 Etag / 坏大小 / 绝对路径 ——
function buildDoc() {
  return {
    scriptVersion: "3.5.4",
    usesBase62EtagsInExport: true,
    files: [
      { path: "115网盘书籍/00000/正常书籍.pdf", size: "123", etag: hexEtag("clean-1") },
      { path: "115网盘书籍/00000/辛亥革命百年纪念文库  癸卯年万岁.zip", size: "456", etag: hexEtag("dirty-2") },
      { path: "115网盘书籍/.stfolder/.aria2/缓存.aria2", size: "789", etag: hexEtag("hidden-3") },
      { path: "115网盘书籍/00000/坏etag.zip", size: "12", etag: "zz!不合法" },
      { path: "115网盘书籍/00000/坏大小.zip", size: "-5", etag: hexEtag("badsize-5") },
      { path: "115网盘书籍/00000/#临时$备份%.zip", size: "55", etag: hexEtag("sanitize-6") },
      { path: "/绝对路径/书.pdf", size: "66", etag: hexEtag("abs-7") }
    ]
  };
}
const expectValid = [
  { path: "115网盘书籍/00000/正常书籍.pdf", fileName: "正常书籍.pdf", etag: hexEtag("clean-1"), size: 123 },
  { path: "115网盘书籍/00000/辛亥革命百年纪念文库 癸卯年万岁.zip", fileName: "辛亥革命百年纪念文库 癸卯年万岁.zip", etag: hexEtag("dirty-2"), size: 456, sanitized: true },
  { path: "115网盘书籍/00000/临时备份.zip", fileName: "临时备份.zip", etag: hexEtag("sanitize-6"), size: 55, sanitized: true }
];
const expectInvalid = { total: 4, sanitized: 0, reasons: { path: 1, etag: 1, size: 1, hidden: 1 } };

// —— 1. 严格模式不变：脏路径依旧整包抛错（拆分/转换等入口零行为变化） ——
{
  assert.throws(() => parseFastlinkJson(buildDoc()), /包含不支持的目录或字符/);
  assert.throws(() => resolveImportedPathParts(".stfolder/缓存.aria2", "文件路径", false), /包含不支持的目录或字符/);
  console.log("ok 严格模式：脏路径/隐藏文件仍然抛错，行为不变");
}

// —— 2. 导入语义（sanitize）：清理后导入 + 隐藏文件/坏条目跳过计数 ——
{
  const collector = createFastlinkInvalidCollector();
  const parsed = parseFastlinkJson(buildDoc(), { sanitize: true, invalidCollector: collector });
  assert.equal(JSON.stringify(parsed.files), JSON.stringify(expectValid));
  assert.equal(collector.summary.total, expectInvalid.total);
  sameJson(collector.summary.reasons, expectInvalid.reasons);
  assert.equal(collector.summary.sanitized, 2, "两条清理路径应计入 sanitized");
  assert.ok(collector.summary.samples.some((sample) => sample.message.includes("隐藏")), "隐藏文件样本应带隐藏提示");
  console.log("ok 导入语义：3 条有效导入（2 条自动清理），4 条跳过（路径/隐藏/Etag/大小各 1）");
}

// —— 3. 流式（sanitize）与文本路径严格一致，任意切块边界稳定 ——
{
  const text = JSON.stringify(buildDoc());
  for (const seed of [7, 42, 2026]) {
    const collector = createFastlinkInvalidCollector();
    const scanner = createFastlinkJsonScanner({ sanitize: true, invalidCollector: collector });
    const streamed = streamParse(text, scanner, { seed });
    assert.equal(JSON.stringify(streamed.files), JSON.stringify(expectValid));
    sameJson(streamed.invalid.reasons, expectInvalid.reasons);
    assert.equal(streamed.invalid.total, expectInvalid.total);
    assert.equal(streamed.invalid.sanitized, 2);
    const textCollector = createFastlinkInvalidCollector();
    const parsed = parseFastlinkJson(JSON.parse(text), { sanitize: true, invalidCollector: textCollector });
    assert.equal(JSON.stringify(parsed.files), JSON.stringify(streamed.files));
    sameJson(textCollector.summary.reasons, streamed.invalid.reasons);
  }
  console.log("ok 流式与文本路径（sanitize）结果严格一致，3 组随机切块全部稳定");
}

// —— 4. 批次模式：攒满交付、finish 不再驻留条目、批次拼接与全量一致 ——
{
  const text = JSON.stringify(buildDoc());
  const batches = [];
  const collector = createFastlinkInvalidCollector();
  const scanner = createFastlinkJsonScanner({ sanitize: true, invalidCollector: collector, batchSize: 2 });
  const streamed = streamParse(text, scanner, { seed: 99 });
  for (;;) {
    const batch = scanner.takeBatch();
    if (!batch) break;
    batches.push(batch);
  }
  assert.ok(batches.length >= 2, `应交付多批，实际 ${batches.length} 批`);
  const flat = batches.flat();
  assert.equal(JSON.stringify(flat), JSON.stringify(expectValid));
  assert.equal(streamed.files.length, 0, "批次模式下 finish() 不应再携带条目");
  sameJson(streamed.invalid.reasons, expectInvalid.reasons);
  console.log(`ok 批次模式：${batches.length} 批交付 ${flat.length} 条有效条目，内存不驻留全量`);
}

// —— 5. commonPath：头部解析完成即可读；批次条目不含公共路径（导入器负责拼接） ——
{
  const doc = { commonPath: "115网盘书籍/115图书", usesBase62EtagsInExport: true, files: [{ path: "00000/a.pdf", size: "1", etag: hexEtag("cp-1") }] };
  const collector = createFastlinkInvalidCollector();
  const scanner = createFastlinkJsonScanner({ sanitize: true, invalidCollector: collector, batchSize: 8 });
  const text = JSON.stringify(doc);
  const headEnd = text.indexOf('"files"') + '"files"'.length + 2; // [[ 之前，commonPath 一定已解析
  scanner.push(text.slice(0, headEnd));
  assert.equal(scanner.getCommonPath(), "115网盘书籍/115图书", "头部解析完 commonPath 即可读");
  scanner.push(text.slice(headEnd));
  const streamed = scanner.finish();
  const batch = scanner.takeBatch();
  assert.ok(batch && batch.length === 1, "应交付一批");
  assert.equal(batch[0].path, "00000/a.pdf", "批次条目不应携带公共路径前缀");
  assert.equal(scanner.takeBatch(), null, "不应再有剩余批次");
  assert.equal(streamed.commonPath, "115网盘书籍/115图书/");
  assert.equal(streamed.files.length, 0, "批次模式 finish() 不携带条目");
  console.log("ok commonPath：头部即可读、批次条目不带前缀");
}

// —— 6. doneSet：64 位指纹增删查、尾巴归并、bulk load ——
{
  const set = createFastlinkDoneSet();
  const hexes = Array.from({ length: 5000 }, (_, index) => fastlinkKeyFingerprint(`key-${index}`));
  const shuffled = [...hexes];
  const rng = makeRng(11);
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(rng() * (index + 1));
    [shuffled[index], shuffled[swap]] = [shuffled[swap], shuffled[index]];
  }
  for (const hex of shuffled) assert.equal(set.add(hex), true, "首次添加应成功");
  assert.equal(set.size, 5000);
  for (const hex of hexes) {
    assert.equal(set.has(hex), true);
    assert.equal(set.add(hex), false, "重复添加应返回 false");
  }
  assert.equal(set.has(fastlinkKeyFingerprint("missing")), false);
  const other = createFastlinkDoneSet();
  other.load(hexes.slice(0, 3000));
  assert.equal(other.size, 3000);
  assert.equal(other.has(hexes[2999]), true);
  assert.equal(other.has(hexes[3999]), false);
  console.log("ok doneSet：5000 指纹（含尾巴归并）增删查与 bulk load 正确");
}

// —— 7. 流式导入器：批量消费、断点续传（v3 指纹 done 集）、失败样本、全清档 ——
{
  const makeApi = (failEtas) => {
    const transfers = [];
    return {
      transfers,
      api: {
        async ensurePath(rootId2, parts, cache) {
          let current = String(rootId2 || "0");
          for (const part of parts) {
            const key = `${current}/${part}`;
            if (!cache.has(key)) cache.set(key, Promise.resolve(key));
            current = await cache.get(key);
          }
          return current;
        },
        async reuseFile(file) {
          if (failEtas.has(file.etag)) throw new Error("云端没有可复用的同哈希文件");
          transfers.push(file.path);
          return `id-${transfers.length}`;
        }
      }
    };
  };
  const entries = Array.from({ length: 10 }, (_, index) => ({
    path: `目录${index % 2}/文件${index}.pdf`,
    fileName: `文件${index}.pdf`,
    etag: hexEtag(`entry-${index}`),
    size: index + 1
  }));
  const failEtas = new Set([entries[2].etag, entries[7].etag]);
  const first = makeApi(failEtas);
  const importer1 = createFastlinkStreamImporter(first.api, "root-1", { concurrency: 8 });
  for (let offset = 0; offset < entries.length; offset += 4) await importer1.consume(entries.slice(offset, offset + 4));
  const result1 = await importer1.finish({ invalid: null });
  assert.equal(result1.ok, 8);
  assert.equal(result1.fail, 2);
  assert.equal(result1.status, "partial");
  assert.equal(result1.invalid, 0);
  assert.ok(result1.details.length === 2 && result1.details.every((item) => item.status === "failed"));
  const savedRecord = JSON.parse(store.get(FASTLINK_IMPORT_STREAM_CHECKPOINT_KEY) || "null");
  assert.equal(savedRecord?.version, 3, "失败收尾应落 v3 断点");

  // 重跑同根目录：done 集命中 8 条跳过，只重试上次失败的 2 条，全部成功后清档
  const second = makeApi(new Set());
  const importer2 = createFastlinkStreamImporter(second.api, "root-1", { concurrency: 8 });
  for (let offset = 0; offset < entries.length; offset += 4) await importer2.consume(entries.slice(offset, offset + 4));
  const result2 = await importer2.finish({ invalid: null });
  assert.equal(result2.skipped, 8, "上次成功的 8 条应按指纹命中跳过");
  assert.equal(result2.ok, 2);
  assert.equal(result2.fail, 0);
  assert.equal(result2.status, "success");
  assert.equal(second.transfers.length, 2, "只应重转上次失败的 2 条");
  assert.equal(store.has(FASTLINK_IMPORT_STREAM_CHECKPOINT_KEY), false, "全部成功后应清档");

  // 异根目录不复用断点：同样 10 条全部重转
  const third = makeApi(new Set());
  const importer3 = createFastlinkStreamImporter(third.api, "root-2", { concurrency: 8 });
  for (let offset = 0; offset < entries.length; offset += 4) await importer3.consume(entries.slice(offset, offset + 4));
  const result3 = await importer3.finish({ invalid: null });
  assert.equal(result3.skipped, 0);
  assert.equal(result3.ok, 10);
  console.log("ok 流式导入器：8+2 断点续传、指纹命中跳过、全成功清档、异根目录隔离");
}

// —— 8. 端到端：大 JSON 文件流式导入（resolveAndImportFastlinkInput），脏内容不再整包失败 ——
{
  const doc = buildDoc();
  doc.files.push(...Array.from({ length: 30 }, (_, index) => ({
    path: `115网盘书籍/分类${index % 3}/书籍${index}.pdf`,
    size: String(100 + index),
    etag: hexEtag(`bulk-${index}`)
  })));
  const transfers = [];
  const api = {
    async ensurePath(rootId2, parts, cache) {
      let current = String(rootId2 || "0");
      for (const part of parts) {
        const key = `${current}/${part}`;
        if (!cache.has(key)) cache.set(key, Promise.resolve(key));
        current = await cache.get(key);
      }
      return current;
    },
    async reuseFile(file) {
      transfers.push(file.path);
      return `id-${transfers.length}`;
    }
  };
  // Blob.size 是原型 getter：用 defineProperty 遮蔽成大文件，强制走流式批次分支
  const fakeBigFile = new Blob([JSON.stringify(doc)]);
  Object.defineProperty(fakeBigFile, "size", { value: 2452144075 });
  fakeBigFile.name = "数字涵芳阁.json";
  const progressLabels = [];
  const result = await resolveAndImportFastlinkInput(api, { file: fakeBigFile }, "root-9", {
    concurrency: 8,
    onProgress: (done, total, name) => progressLabels.push(name)
  });
  assert.equal(result.ok, 3 + 30, "3 条原有有效 + 30 条批量有效");
  assert.equal(result.fail, 0);
  assert.equal(result.invalid, 4);
  assert.equal(result.sanitized, 2);
  sameJson(result.invalidReasons, expectInvalid.reasons);
  assert.ok(result.status === "success");
  assert.ok(progressLabels.some((label) => label.includes("流式读取")), "应有流式读取进度");
  assert.ok(store.has(FASTLINK_IMPORT_STREAM_CHECKPOINT_KEY) === false, "全成功应清档");
  console.log(`ok 端到端流式导入：${result.ok} 条成功、${result.invalid} 条跳过（隐藏/坏条目不再整包中止）`);
}

// —— 9. importFastlink 文本路径：字符串输入同样自动清理 + 跳过计数 ——
{
  const transfers = [];
  const api = {
    async findChildFolder() {
      return null;
    },
    async createFolder(parentId, name) {
      return `${parentId}/${name}`;
    },
    async ensurePath(rootId2, parts, cache) {
      let current = String(rootId2 || "0");
      for (const part of parts) {
        const key = `${current}/${part}`;
        if (!cache.has(key)) cache.set(key, Promise.resolve(key));
        current = await cache.get(key);
      }
      return current;
    },
    async reuseFile(file) {
      transfers.push(file.path);
      return `id-${transfers.length}`;
    }
  };
  const result = await importFastlink(api, JSON.stringify(buildDoc()), "root-text", { concurrency: 8 });
  assert.equal(result.ok, 3);
  assert.equal(result.fail, 0);
  assert.equal(result.invalid, 4);
  assert.equal(result.sanitized, 2);
  sameJson(result.invalidReasons, expectInvalid.reasons);
  assert.equal(transfers.length, 3);
  assert.ok(transfers.includes("115网盘书籍/00000/辛亥革命百年纪念文库 癸卯年万岁.zip"), "清理后的文件名应落库（双空格→单空格）");
  console.log("ok 文本/种子路径：字符串输入同样清理导入 + 无效计数透传");
}
