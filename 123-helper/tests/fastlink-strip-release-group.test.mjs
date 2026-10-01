// 秒传导出「去除发布组」回归测试：验证秒传设置里的 stripReleaseGroup 开关在各类导出产物里
// 生效（JSON / V2 链接 / 拆分 / 格式转换 / 二级链接种子内容），识别沿用整理的发布组规则 +
// 设置里的「特殊发布组」名单，并且：默认关不改任何行为、只动文件名末段不动目录名、
// 去掉组名后撞名的那条保留原名、二级链接指向的种子文件名与实际上传的文件名一致。
// 用法：node 123-helper/tests/fastlink-strip-release-group.test.mjs
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
  slice("// src/api.js", "// src/config.js"),
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js"),
  slice("// src/core/release-group.js", "// src/core/rename.js")
].join("\n");
const driver = `;
globalThis.__strip = { buildFastlinkJson, buildFastlinkText, splitFastlink, convertJsonTo123Share, convert123ShareToJson, generateSecondaryFastlink, base64ToBytes };
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
const { buildFastlinkJson, buildFastlinkText, splitFastlink, convertJsonTo123Share, convert123ShareToJson, generateSecondaryFastlink, base64ToBytes } = sandbox.__strip;

const ETAG = "a".repeat(32);
const ON = { stripReleaseGroup: true };
const entry = (path, size = 10) => ({ path, fileName: path.split("/").at(-1), etag: ETAG, size });
const data = (json) => JSON.parse(json);
const sorted = (list) => [...list].sort();
const paths = (json) => sorted(data(json).files.map((file) => file.path));

// —— 用例 1：默认关（不传选项 / 显式 false）导出内容一字不改 ——
{
  const files = [entry("剧/Show.S01E01.1080p.WEB-DL.H264-AAWeb.mkv"), entry("剧/Show.S01E02.1080p.WEB-DL.H264 [WiKi].mkv")];
  const plain = paths(buildFastlinkJson(files));
  assert.deepEqual(plain, sorted(["Show.S01E01.1080p.WEB-DL.H264-AAWeb.mkv", "Show.S01E02.1080p.WEB-DL.H264 [WiKi].mkv"]), "默认不勾选时文件名应原样保留");
  assert.deepEqual(paths(buildFastlinkJson(files, { stripReleaseGroup: false })), plain, "显式 false 与不传选项等价");
  assert.ok(buildFastlinkText(files).includes("-AAWeb"), "V2 链接默认保留组名");
  assert.ok(!buildFastlinkText(files, ON).includes("-AAWeb"), "V2 链接勾选后去掉组名");
  assert.ok(buildFastlinkText(files, ON).startsWith("123FLCPV2$剧/%"), "V2 链接的公共目录前缀不受影响");
  console.log("ok 默认关闭：JSON 与 V2 链接一字不改");
}

// —— 用例 2：开关打开，连字符 / 方括号 / 来源@组名 三种写法都去掉，path 与 fileName 同步 ——
{
  const files = [
    entry("剧/Show.S01E01.1080p.WEB-DL.H264-AAWeb.mkv"),
    entry("剧/Show.S01E02.1080p.WEB-DL.H264 [WiKi].mkv"),
    entry("剧/Show.S01E03.1080p.WEB-DL.H264.NF@ADWeb.mkv")
  ];
  assert.deepEqual(paths(buildFastlinkJson(files, ON)), sorted([
    "Show.S01E01.1080p.WEB-DL.H264.mkv",
    "Show.S01E02.1080p.WEB-DL.H264.mkv",
    "Show.S01E03.1080p.WEB-DL.H264.mkv"
  ]));
  assert.deepEqual(sorted(data(buildFastlinkJson(files, ON)).files.map((file) => file.fileName)), sorted(data(buildFastlinkJson(files, ON)).files.map((file) => file.path)), "fileName 字段要与 path 末段一致");
  const link = buildFastlinkText(files, ON);
  assert.ok(!/AAWeb|\[WiKi\]|WiKi|ADWeb/.test(link), "V2 链接条目里不该留组名");
  assert.equal(data(buildFastlinkJson(files, ON)).commonPath, "剧/", "目录前缀保持");
  console.log("ok 勾选后：三种发布组写法都去掉，fileName 同步");
}

// —— 用例 3：设置里的「特殊发布组」名单参与识别；名单外的裸组名不误伤 ——
{
  const files = [entry("Show.S01E01.HiveWeb.mkv"), entry("Show.S01E02.HiveWeb.mkv")];
  assert.deepEqual(paths(buildFastlinkJson(files, ON)), sorted(["Show.S01E01.HiveWeb.mkv", "Show.S01E02.HiveWeb.mkv"]), "文件名没有技术上下文时不猜组名");
  const withList = { stripReleaseGroup: true, releaseGroups: ["HiveWeb"] };
  assert.deepEqual(paths(buildFastlinkJson(files, withList)), sorted(["Show.S01E01.mkv", "Show.S01E02.mkv"]), "名单里的组名照样去掉");
  // 极端命名：整个文件名就是组名，去掉只剩扩展名 → 保持原样
  assert.deepEqual(paths(buildFastlinkJson([entry("HiveWeb.mkv")], withList)), ["HiveWeb.mkv"], "整名即组名时不改名");
  console.log("ok 特殊发布组名单生效，名单外与整名即组名都不误伤");
}

// —— 用例 3b：流媒体 WEB-DL 命名里「编码/音效-来源」组合不是发布组，别误去（与 Python 端同步）——
{
  const files = [
    entry("剧/Fakhar.S01E01.1080p.WEB-DL.AAC2.0.H.264-NF.mkv"),
    entry("剧/Fakhar.S01E02.2026.1080p.WEB-DL.AVC.DDP.5.1.Atmos-NF.mkv"),
    entry("剧/Fakhar.S01E03.1080p.WEB-DL.x264-DSNP.mkv")
  ];
  assert.deepEqual(paths(buildFastlinkJson(files, ON)), sorted([
    "Fakhar.S01E01.1080p.WEB-DL.AAC2.0.H.264-NF.mkv",
    "Fakhar.S01E02.2026.1080p.WEB-DL.AVC.DDP.5.1.Atmos-NF.mkv",
    "Fakhar.S01E03.1080p.WEB-DL.x264-DSNP.mkv"
  ]), "来源标签 NF/DSNP 与前面编码/音效粘成的段不是发布组，勾选去组也不该动");
  // 真发布组（连字符尾段 NTb）照常去掉
  const real = buildFastlinkJson([entry("剧/Show.S01E01.1080p.WEB-DL.H264-NTb.mkv")], ON);
  assert.deepEqual(paths(real), ["Show.S01E01.1080p.WEB-DL.H264.mkv"], "真发布组 NTb 仍被去掉");
  console.log("ok 流媒体来源标签不误判为发布组，真发布组照常去");
}

// —— 用例 4：只动文件名末段，目录名原样保留（公共前缀里带组名也不改）——
{
  const json = buildFastlinkJson([entry("Show.S01.AAWeb/Season 01.HiveWeb/E01.1080p-WEB2.mkv")], { stripReleaseGroup: true, releaseGroups: ["HiveWeb"] });
  const parsed = data(json);
  assert.equal(parsed.commonPath, "Show.S01.AAWeb/Season 01.HiveWeb/", "目录层级保持原样");
  assert.deepEqual(parsed.files.map((file) => file.path), ["E01.1080p.mkv"]);
  console.log("ok 目录层级不动，只去文件名末尾发布组");
}

// —— 用例 5：去掉组名后撞名的那条保留原名（防秒传落地生成「名字 (1)」副本）——
{
  const twins = [entry("剧/Movie.2020.1080p.x264-GROUP1.mkv"), entry("剧/Movie.2020.1080p.x264-GROUP2.mkv")];
  assert.deepEqual(sorted(data(buildFastlinkJson(twins, ON)).files.map((file) => file.path)), sorted(["Movie.2020.1080p.x264.mkv", "Movie.2020.1080p.x264-GROUP2.mkv"]), "同批互相撞名：后来的保留原名");
  const clash = [entry("剧/Movie.2020.1080p.x264.mkv"), entry("剧/Movie.2020.1080p.x264-GROUP1.mkv")];
  assert.deepEqual(paths(buildFastlinkJson(clash, ON)), sorted(["Movie.2020.1080p.x264.mkv", "Movie.2020.1080p.x264-GROUP1.mkv"]), "与本来就叫这个名字的文件撞名：改名的那条让路");
  console.log("ok 撞名保护：重名的那条保留原名");
}

// —— 用例 6：拆分与格式转换同样按开关去发布组 ——
{
  const source = JSON.stringify({
    commonPath: "",
    files: [
      { path: "剧/Show.S01E01.1080p.WEB-DL.H264-GROUP1.mkv", fileName: "Show.S01E01.1080p.WEB-DL.H264-GROUP1.mkv", etag: ETAG, size: 10 },
      { path: "剧/Show.S01E02.1080p.WEB-DL.H264-GROUP2.mkv", fileName: "Show.S01E02.1080p.WEB-DL.H264-GROUP2.mkv", etag: ETAG, size: 20 }
    ]
  });
  const stripped = splitFastlink(source, "folder", 1, ON);
  assert.equal(stripped.length, 1);
  assert.deepEqual(paths(stripped[0].text), sorted(["Show.S01E01.1080p.WEB-DL.H264.mkv", "Show.S01E02.1080p.WEB-DL.H264.mkv"]));
  assert.ok(splitFastlink(source, "folder", 1)[0].text.includes("GROUP1"), "不勾选时拆分保持原样");

  const shareSource = JSON.stringify({ commonPath: "", files: [{ path: "Movie.2020.1080p.x264-GROUP1.mkv", fileName: "Movie.2020.1080p.x264-GROUP1.mkv", etag: ETAG, size: 10 }] });
  const decode = (value) => JSON.parse(new TextDecoder().decode(base64ToBytes(value)));
  assert.equal(decode(convertJsonTo123Share(shareSource, ON)).find((item) => item.Type === 0).FileName, "Movie.2020.1080p.x264.mkv", "JSON→123share 去组名");
  assert.equal(decode(convertJsonTo123Share(shareSource)).find((item) => item.Type === 0).FileName, "Movie.2020.1080p.x264-GROUP1.mkv", "不勾选时转换保持原样");
  const b64Share = convertJsonTo123Share(shareSource);
  assert.deepEqual(convert123ShareToJson(b64Share, ON).files.map((file) => file.path), ["Movie.2020.1080p.x264.mkv"], "123share→JSON 去组名");
  assert.deepEqual(convert123ShareToJson(b64Share).files.map((file) => file.path), ["Movie.2020.1080p.x264-GROUP1.mkv"], "不勾选时 123share→JSON 保持原样");
  assert.deepEqual(convert123ShareToJson(b64Share, { raw: true }).files.map((file) => file.path), ["Movie.2020.1080p.x264-GROUP1.mkv"], "导入用的 raw 解析绝不改名");
  console.log("ok 拆分 / JSON↔123share 转换同样按开关去发布组");
}

// —— 用例 7：二级链接——种子内容去掉发布组，短链里的种子文件名与实际上传一致 ——
{
  const folders = new Map([
    ["f1", [
      { id: "v1", name: "Show.S01E01.1080p.WEB-DL.H264-GROUP1.mkv", type: 0, size: 100, etag: ETAG },
      { id: "v2", name: "Show.S01E02.1080p.WEB-DL.H264-GROUP2.mkv", type: 0, size: 200, etag: ETAG }
    ]]
  ]);
  const uploaded = [];
  const api = {
    async listAll(id) { return folders.get(String(id)) || []; },
    async fileInfos() { return []; },
    async uploadTextFile(fileName, content, parentId) {
      uploaded.push({ fileName, content, parentId });
      return { id: "seed1", etag: ETAG, size: content.length };
    }
  };
  const items = [{ id: "f1", name: "剧集", type: 1 }];
  const artifact = await generateSecondaryFastlink(api, items, { ...ON, useJson: true, useFolderName: true, seedFolderId: "9" });
  assert.equal(uploaded[0].parentId, "9");
  assert.deepEqual(data(uploaded[0].content).files.map((file) => file.path).sort(), sorted(["Show.S01E01.1080p.WEB-DL.H264.mkv", "Show.S01E02.1080p.WEB-DL.H264.mkv"]), "种子内容里的文件名应去掉发布组");
  assert.equal(artifact.strippedReleaseGroups, "GROUP1/GROUP2", "去组时要在原名被洗掉前把发布组收集出来，供直投 meta 带回客户端做路由");
  assert.equal(artifact.link.split("#").at(-1), uploaded[0].fileName, "短链指向的名字必须就是实际上传的种子文件名");
  const txtArtifact = await generateSecondaryFastlink(api, items, { ...ON, useJson: false, useFolderName: true, seedFolderId: "9" });
  assert.ok(!/GROUP1|GROUP2/.test(txtArtifact.text), "txt 形态的种子内容同样去掉发布组");
  const plainArtifact = await generateSecondaryFastlink(api, items, { useJson: true, useFolderName: true, seedFolderId: "9" });
  assert.ok(plainArtifact.text.includes("GROUP1"), "不勾选时种子内容保持原名");
  assert.equal(plainArtifact.strippedReleaseGroups, "", "不勾选时不收集发布组（客户端照常自己识别）");
  console.log("ok 二级链接：内容去组名，短链文件名与实际种子一致");
}

// —— 用例 8：设置归一——老配置没这个键按关处理，只认严格 true ——
{
  const configCode = [
    "function applySpecialKeywordMappings() {}",
    slice("// src/core/utils.js", "// src/api.js"),
    slice("// src/api.js", "// src/config.js"),
    slice("// src/config.js", "// node_modules/lucide/dist/esm/createElement.js")
  ].join("\n");
  const configStore = new Map();
  const configSandbox = {
    console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
    DOMException, BigInt, TextEncoder, TextDecoder, setTimeout, clearTimeout,
    location: { origin: "https://www.123pan.cn" },
    navigator: { language: "zh-CN" },
    localStorage: {
      getItem: (key) => (configStore.has(key) ? configStore.get(key) : null),
      setItem: (key, value) => configStore.set(key, String(value)),
      removeItem: (key) => configStore.delete(key)
    }
  };
  vm.createContext(configSandbox);
  vm.runInContext(configCode + ";globalThis.__cfg = { normalizeConfig };", configSandbox, { filename: "123-helper.user.js" });
  const { normalizeConfig } = configSandbox.__cfg;
  assert.equal(normalizeConfig({}).fastlinkTools.stripReleaseGroup, false, "默认关闭");
  assert.equal(normalizeConfig({ fastlinkTools: { debugMode: true, filters: [] } }).fastlinkTools.stripReleaseGroup, false, "老配置没这个键 → 关");
  assert.equal(normalizeConfig({ fastlinkTools: { stripReleaseGroup: true } }).fastlinkTools.stripReleaseGroup, true, "勾选后落库为 true");
  assert.equal(normalizeConfig({ fastlinkTools: { stripReleaseGroup: "yes" } }).fastlinkTools.stripReleaseGroup, false, "脏值按关处理");
  assert.equal(normalizeConfig({ library: { recognition: { releaseGroups: [" HiveWeb ", ""] } } }).library.recognition.releaseGroups.slice(0, 1).join("|"), "HiveWeb", "特殊发布组名单去空白后传给导出");
  console.log("ok 配置归一：默认关、只认严格 true、名单去空白");
}

// —— 用例 9：设置页勾选与控制器透传的接线（防后续改动把入口或参数链拆断）——
{
  const bundle = lines.join("\n");
  assert.ok(bundle.includes('data-fastlink-setting="stripReleaseGroup"'), "秒传设置页要有「导出时去除文件名发布组」勾选");
  const exportOptions = bundle.slice(bundle.indexOf("fastlinkExportOptions() {"), bundle.indexOf("fastlinkTransferOptions() {"));
  assert.ok(exportOptions.includes("stripReleaseGroup: settings.stripReleaseGroup === true"), "导出参数要带开关");
  assert.ok(exportOptions.includes("releaseGroups: this.config.library?.recognition?.releaseGroups || []"), "导出参数要带特殊发布组名单");
  const shareStart = bundle.indexOf("if (isPublicShareHost()) {");
  const shareInstall = bundle.slice(shareStart, bundle.indexOf("const start = () => {", shareStart));
  assert.ok(shareInstall.includes("stripReleaseGroup: publicConfig.fastlinkTools?.stripReleaseGroup === true"), "分享页的生成秒传也要吃到这个设置");
  assert.equal((bundle.match(/buildFastlinkJson\(filtered, options\)/g) || []).length, 2, "分享页搜索与官方搜索的生成秒传都要按设置去发布组");
  assert.ok(bundle.includes("{ ...options, stripReleaseGroup: false }"), "二级短链里的种子文件名要与实际上传的一致，不参与去组名");
  // 直投标记：勾选去发布组后推给客户端的这条要带 skipReleaseGroup，客户端不再乱认发布组
  const pushSlice = bundle.slice(bundle.indexOf("async pushFastlinkSubmissionDraft(artifact) {"), bundle.indexOf("async restoreFastlinkFromCloudFile() {"));
  assert.ok(pushSlice.includes("const skipReleaseGroup = (this.config.fastlinkTools || {}).stripReleaseGroup === true;"), "直投要按开关决定 skipReleaseGroup");
  assert.ok(pushSlice.includes("sourceText, skipReleaseGroup, meta }"), "skipReleaseGroup 与 meta 要随种子条目一起提交");
  assert.ok(bundle.includes("...(item.skipReleaseGroup ? { skipReleaseGroup: true } : {})"), "submitShares 只在该条目带了标记时才提交 skipReleaseGroup 字段");
  console.log("ok 设置页勾选与控制器参数链接线在位");
}

console.log("fastlink-strip-release-group 全部通过");