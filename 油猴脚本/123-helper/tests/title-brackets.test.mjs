// 标题括号识别 + TMDB 查询解析回归测试：
// 1) 【频道/字幕组/标记】剧名 不再把前缀标记当标题（【中国广电重温经典频道】黑猫警长 → 黑猫警长）；
// 2) 手动查询「【xxx】剧名」解析出的搜索词是剧名本身；
// 3) mediaSource 与发布组的「来源@发布组」拆分（NF@ADWeb → 来源 NF、发布组 ADWeb）。
// 用法：node 油猴脚本/tests/title-brackets.test.mjs
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
// 顺序与 bundle 一致：release-group 段在 recognition-maps（含 DEFAULT_FIXED_MAPPINGS）之后
const code = [
  slice("// src/core/utils.js", "// src/api.js"),
  slice("// src/core/categories.js", "// src/config.js"),
  slice("// src/core/release-group.js", "// src/core/rename.js"),
  slice("// src/core/category-yaml.js", "// src/core/template.js"),
  slice("// src/core/organize.js", "// src/core/organize-strategy.js")
].join("\n");
const driver = `;
globalThis.__brackets = { inferTitle, parseTmdbLookupQuery, inferMediaType, extractReleaseGroup, inferTechnicalFields };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakMap, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { inferTitle, parseTmdbLookupQuery, inferMediaType, extractReleaseGroup, inferTechnicalFields } = sandbox.__brackets;

let passed = 0;
const test = (title, fn) => {
  fn();
  passed += 1;
  console.log(`  ok ${title}`);
};

// —— 【】前缀标记不再抢占标题 ——
test("频道前缀：【中国广电重温经典频道】剧名取剧名", () => {
  assert.equal(inferTitle("【中国广电重温经典频道】黑猫警长"), "黑猫警长");
  assert.equal(inferTitle("【中国广电重温经典频道】黑猫警长 1984 全5集 1080P"), "黑猫警长");
});
test("频道前缀带年份版号：【重温经典频道】西游记1986版 → 西游记", () => {
  assert.equal(inferTitle("【重温经典频道】西游记1986版"), "西游记");
});
test("字幕组前缀：[阳马工作室] 葬送的芙莉莲 取括号外标题", () => {
  assert.equal(inferTitle("[阳马工作室] 葬送的芙莉莲 [01] [1080p] [简繁内封]"), "葬送的芙莉莲");
});
test("组名前缀短词：【剧般若】苍兰诀【全36集】【4K简繁】取剧名", () => {
  assert.equal(inferTitle("【剧般若】苍兰诀【全36集】【4K简繁】"), "苍兰诀");
});
test("CCTV 频道前缀：【CCTV-8 电视剧频道】狂飙 → 狂飙", () => {
  assert.equal(inferTitle("【CCTV-8 电视剧频道】狂飙"), "狂飙");
});

// —— 原有正确场景不回归 ——
test("纯标题括号：【庆余年】【第一季】→ 庆余年", () => {
  assert.equal(inferTitle("【庆余年】【第一季】"), "庆余年");
  assert.equal(inferTitle("【庆余年】第一季全集"), "庆余年");
});
test("括号外是注记时仍取括号内：【琅琊榜】DVD全集 → 琅琊榜", () => {
  assert.equal(inferTitle("【琅琊榜】DVD全集"), "琅琊榜");
  assert.equal(inferTitle("【甄嬛传】国语版"), "甄嬛传");
});
test("【主标题】副标题篇 保留括号内主标题", () => {
  assert.equal(inferTitle("【凡人修仙传】星海飞驰篇【60帧】"), "凡人修仙传");
});
test("年份/季集括号不误伤：【黑猫警长】1984 → 黑猫警长", () => {
  assert.equal(inferTitle("【黑猫警长】1984"), "黑猫警长");
  assert.equal(inferTitle("【1984】黑猫警长"), "黑猫警长");
});
test("标题尾部「76集全」剥离", () => {
  assert.equal(inferTitle("【国语】甄嬛传 76集全"), "甄嬛传");
});
test("技术标签括号：【4K】沙丘 Dune.2021.WEB-DL.2160p → 沙丘 Dune", () => {
  assert.equal(inferTitle("【4K】沙丘 Dune.2021.WEB-DL.2160p"), "沙丘 Dune");
});
test("第一部留在标题里不丢剧名：【家有儿女第一部】", () => {
  assert.equal(inferTitle("【家有儿女第一部】"), "家有儿女第一部");
});

// —— 同括号「频道前缀+剧名」混排 ——
test("频道前缀与剧名同括号：【四川卫视4K超高清频道 故乡几万里】取剧名", () => {
  assert.equal(
    inferTitle("[四川卫视4K超高清频道 故乡几万里].SCTV-4K.My.Hometown.Across.The.Ocean.2024.S01.2160p.50fps.UHDTV.AVS2.10bit.HLG.DD2.0-QHstudIo"),
    "故乡几万里",
  );
});
test("频道代码+剧名同括号：【CCTV-8 电视剧频道 狂飙】取剧名", () => {
  assert.equal(inferTitle("【CCTV-8 电视剧频道 狂飙】"), "狂飙");
  assert.equal(inferTitle("【SCTV-4K 故乡几万里】"), "故乡几万里");
});
test("技术注记打头的混排括号：【4K 超高清频道 故乡几万里】取剧名", () => {
  assert.equal(inferTitle("【4K 超高清频道 故乡几万里】"), "故乡几万里");
  assert.equal(inferTitle("【国语 狂飙】"), "狂飙");
});
test("混排括号剥光标记仍是标记：【4K 全36集】不当地名", () => {
  assert.equal(inferTitle("【4K 全36集】黑猫警长"), "黑猫警长");
});
test("纯标题括号不误剥：【家有儿女第一部】不动", () => {
  assert.equal(inferTitle("【家有儿女第一部】"), "家有儿女第一部");
  assert.equal(inferTitle("【庆余年】【第一季】"), "庆余年");
});

// —— 括号外频道代码前缀（标记独占括号的变体）——
test("标记独占括号时 SCTV-4K 频道代码不粘进英文标题", () => {
  assert.equal(
    inferTitle("[四川卫视4K超高清频道].SCTV-4K.My.Hometown.Across.The.Ocean.2024.S01.2160p.UHDTV.AVS2.10bit.HLG.DD2.0-QHstudIo"),
    "My Hometown Across The Ocean",
  );
  assert.equal(inferTitle("CCTV-1 开讲啦 20240101"), "开讲啦");
});
test("TV 开头的剧名不被频道代码剥离误伤", () => {
  assert.equal(inferTitle("TV Patrol 2024.1080p.HDTV-GROUP"), "TV Patrol");
});

// —— 手动查询解析联动 ——
test("查询带频道前缀时搜索词是剧名", () => {
  const parsed = parseTmdbLookupQuery("【中国广电重温经典频道】黑猫警长");
  assert.equal(parsed.query, "黑猫警长");
  const mixed = parseTmdbLookupQuery("[四川卫视4K超高清频道 故乡几万里].SCTV-4K.My.Hometown.Across.The.Ocean.2024.S01");
  assert.equal(mixed.query, "故乡几万里");
});
test("inferMediaType：全N集/共N集识别为剧集（不再因年份+分辨率误判电影）", () => {
  assert.equal(inferMediaType("【重温经典频道】黑猫警长 1984 全5集 1080P"), "tv");
  assert.equal(inferMediaType("某剧 共40集 4K"), "tv");
  assert.equal(inferMediaType("沙丘 Dune.2021.2160p.WEB-DL"), "movie");
});

// —— 来源@发布组 拆分 ——
test("NF@ADWeb 发布组取 @ 后段", () => {
  assert.equal(extractReleaseGroup("Show.2023.S01.2160p.NF.WEB-DL.DDP5.1.H.264-NF@ADWeb.mkv"), "ADWeb");
  assert.equal(extractReleaseGroup("Show.S01.[NF@ADWeb].mkv"), "ADWeb");
});
test("AMZN/ATVP 等片源前缀同样拆分", () => {
  assert.equal(extractReleaseGroup("Show.2023.S01.1080p.WEB-DL.H.264-AMZN@ADWeb.mkv"), "ADWeb");
});
test("小组@大组 的 PT 记法保持原样", () => {
  assert.equal(extractReleaseGroup("Show.2023.S01.1080p.WEB-DL.AAC.H.264-cXcY@FRDS.mkv"), "cXcY@FRDS");
});
test("普通发布组不变", () => {
  assert.equal(extractReleaseGroup("Show.2023.S01.1080p.WEB-DL.AAC.H.264-ADWeb.mkv"), "ADWeb");
});

// —— Remux 分辨率隔断（联动 technical-fields 主用例）——
test("BluRay.1080p.Remux 识别为 BluRay Remux", () => {
  assert.equal(inferTechnicalFields("The.Movie.2019.BluRay.1080p.Remux.AVC.FLAC.2.0-GROUP").resourceType, "BluRay Remux");
});

console.log(`title-brackets.test.mjs 全部通过（${passed} 项）`);
