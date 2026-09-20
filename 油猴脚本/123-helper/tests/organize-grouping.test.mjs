// 整理分组与变体命名回归测试：直接从 123-helper.user.js bundle 中切出识别与整理模块，
// 覆盖「标题 01 / 第01集」同剧归组、「主标题+短后缀」衍生分组归并、变体命名去重。
// 用法：node 油猴脚本/tests/organize-grouping.test.mjs
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
  slice("// src/core/recognition-maps.js", "// src/config.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");
const driver = `;
globalThis.__organize = {
  inferTitle, mediaKey, buildLooseGroups, stripLooseEpisodeTail, looseGroupBaseTitle,
  looseVariantRemainder, organizeVariantTag, injectNameVariant, synthesizeEpisodeCandidatesFromNames,
  applyVariantTagsForCollisions, parseSeasonEpisode, specialContext, isVideoFile
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { buildLooseGroups, stripLooseEpisodeTail, looseGroupBaseTitle, looseVariantRemainder, organizeVariantTag, injectNameVariant, synthesizeEpisodeCandidatesFromNames, applyVariantTagsForCollisions, inferTitle, parseSeasonEpisode } = sandbox.__organize;

const config = { library: { recognition: { customWords: [] } } };
const file = (name, id = name) => ({ id, name });

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    await fn();
    passed += 1;
    console.log(`  ok ${title}`);
  });
};

// —— 结尾集号剥离 ——
test("stripLooseEpisodeTail 剥离结尾序号/集号", () => {
  assert.equal(stripLooseEpisodeTail("标题 01"), "标题");
  assert.equal(stripLooseEpisodeTail("标题02"), "标题");
  assert.equal(stripLooseEpisodeTail("标题 第03集"), "标题");
  assert.equal(stripLooseEpisodeTail("标题 第一集"), "标题");
  assert.equal(stripLooseEpisodeTail("标题 第01期 上"), "标题");
  assert.equal(stripLooseEpisodeTail("标题 E04"), "标题");
  assert.equal(stripLooseEpisodeTail("初入职场·中医季"), "初入职场·中医季");
});
test("looseGroupBaseTitle 不产生弱标题", () => {
  assert.equal(looseGroupBaseTitle("24"), "24");
  assert.equal(looseGroupBaseTitle("标题 9"), "标题");
});
test("looseVariantRemainder 只接受短衍生后缀", () => {
  assert.equal(looseVariantRemainder("初入职场·中医季 药食同源", "初入职场·中医季"), "药食同源");
  assert.equal(looseVariantRemainder("初入职场·中医季", "初入职场·中医季"), "");
  assert.equal(looseVariantRemainder("初入职场·中医季 第二季 衍生", "初入职场·中医季"), "");
  assert.equal(looseVariantRemainder("初入职场·中医季 2025 特别篇", "初入职场·中医季"), "");
  assert.equal(looseVariantRemainder("别的节目", "初入职场·中医季"), "");
});

// —— 同剧各集归为一组（用户报告：文件名 01 / 02 / 第一集 各成一组）——
test("序号集号命名的各集归入同一分组", () => {
  const files = [
    "标题 01.mp4",
    "标题 02.mp4",
    "标题03.mp4",
    "标题 第四集.mp4",
    "标题 05.mp4"
  ].map((name) => file(name));
  const groups = buildLooseGroups(files, config);
  const videoGroups = groups.filter((group) => group.files.length);
  assert.equal(videoGroups.length, 1, `应为一个分组，实际 ${videoGroups.length}: ${videoGroups.map((group) => group.title).join(" / ")}`);
  assert.equal(videoGroups[0].title, "标题");
  assert.equal(videoGroups[0].files.length, 5);
});

// —— 真实场景：初入职场·中医季（正片上/下 + 药食同源 + 加更版）不再拆组 ——
const EPISODE_FILES = [];
for (let issue = 1; issue <= 8; issue += 1) {
  const stamp = String(20250914 + issue * 7);
  const token = `S05E${String(issue).padStart(2, "0")}`;
  EPISODE_FILES.push(`[${stamp}][初入职场·中医季 第${String(issue).padStart(2, "0")}期 上].Workplace.Newcomers.2025.${token}.Part01.2160p.WEB-DL.H264.AAC-UBWEB.mp4`);
  EPISODE_FILES.push(`[${stamp}][初入职场·中医季 第${String(issue).padStart(2, "0")}期 下].Workplace.Newcomers.2025.${token}.Part02.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
  EPISODE_FILES.push(`[${stamp}][初入职场·中医季 药食同源 第${String(issue).padStart(2, "0")}期].Workplace.Newcomers.Food.Medicine.Homolog.2025.${token}.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
  EPISODE_FILES.push(`[${stamp + 1}][初入职场·中医季 加更版 第${String(issue).padStart(2, "0")}期].Workplace.Newcomers.Extra.Version.2025.${token}.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
}
test("药食同源衍生段并入主分组并携带变体标记", () => {
  const groups = buildLooseGroups(EPISODE_FILES.map((name) => file(name)), config);
  const videoGroups = groups.filter((group) => group.files.some((item) => item.name.endsWith(".mp4")));
  assert.equal(videoGroups.length, 1, `应为一个分组，实际 ${videoGroups.length}: ${videoGroups.map((group) => `${group.title}(${group.files.length})`).join(" / ")}`);
  const [group] = videoGroups;
  assert.equal(group.title, "初入职场·中医季");
  assert.equal(group.files.length, EPISODE_FILES.length);
  const variants = group.files.filter((item) => item.name.includes("药食同源")).map((item) => item.variantLabel);
  assert.ok(variants.length === 8 && variants.every((label) => label === "药食同源"), "药食同源文件应带 variantLabel");
  assert.ok(group.files.filter((item) => !item.name.includes("药食同源")).every((item) => !item.variantLabel), "其余文件不应带 variantLabel");
});

// —— 变体命名：同集号多版本产出不同文件名 ——
test("organizeVariantTag 识别加更/分部标记", () => {
  const extra = "[20250915][初入职场·中医季 加更版 第01期].Workplace.Newcomers.Extra.Version.2025.S05E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  const part1 = "[20250914][初入职场·中医季 第01期 上].Workplace.Newcomers.2025.S05E01.Part01.2160p.WEB-DL.H264.AAC-UBWEB.mp4";
  const part2 = "[20250914][初入职场·中医季 第01期 下].Workplace.Newcomers.2025.S05E01.Part02.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  const regular = "[20250921][初入职场·中医季 第02期].Show.2025.S05E02.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  assert.equal(organizeVariantTag(file(extra), { specialStrong: true, matchedToSpecial: false }), "加更版");
  assert.equal(organizeVariantTag(file(extra), { specialStrong: true, matchedToSpecial: true }), "");
  assert.equal(organizeVariantTag(file(part1), {}), "Part01");
  assert.equal(organizeVariantTag(file(part2), {}), "Part02");
  assert.equal(organizeVariantTag(file("某综艺 第3期 上.2160p.mp4"), {}), "上");
  assert.equal(organizeVariantTag(file(regular), { specialStrong: false, matchedToSpecial: false }), "");
  assert.equal(organizeVariantTag(file(extra), { variantLabel: "药食同源" }), "药食同源");
});
test("injectNameVariant 在季集记号后插入变体且幂等", () => {
  const base = "初入职场·中医季.2025.S05E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  assert.equal(injectNameVariant(base, "加更版"), "初入职场·中医季.2025.S05E01.加更版.2160p.WEB-DL.H265.AAC-UBWEB.mp4");
  assert.equal(injectNameVariant(base, "Part02"), "初入职场·中医季.2025.S05E01.Part02.2160p.WEB-DL.H265.AAC-UBWEB.mp4");
  assert.equal(injectNameVariant("初入职场·中医季.2025.2160p.WEB-DL.mp4", "Part02"), "初入职场·中医季.2025.2160p.WEB-DL.Part02.mp4");
  const tagged = injectNameVariant(base, "药食同源");
  assert.equal(injectNameVariant(tagged, "药食同源"), tagged);
  assert.equal(injectNameVariant(base, ""), base);
});

// —— 变体标记仅在重名冲突时插入 ——
test("applyVariantTagsForCollisions：集号唯一时不加变体，冲突时才插入", () => {
  const task = (name, tag) => ({ normalizedName: name, newName: name, targetPath: `综艺/Season 5/${name}`, folderParts: ["综艺", "Season 5"], variantTag: tag, hasManualName: false });
  // 校准后各集号唯一：Part01/药食同源 标记不应出现
  const calibrated = [
    task("Workplace Newcomers.2021.S05E01.2160p.WEB-DL.H264.AAC-UBWEB.mp4", "Part01"),
    task("Workplace Newcomers.2021.S05E02.2160p.WEB-DL.H265.AAC-UBWEB.mp4", "Part02"),
    task("Workplace Newcomers.2021.S00E100.2160p.WEB-DL.H265.AAC-UBWEB.mp4", "药食同源")
  ];
  applyVariantTagsForCollisions(calibrated);
  assert.equal(calibrated[0].newName, "Workplace Newcomers.2021.S05E01.2160p.WEB-DL.H264.AAC-UBWEB.mp4");
  assert.equal(calibrated[2].newName, "Workplace Newcomers.2021.S00E100.2160p.WEB-DL.H265.AAC-UBWEB.mp4");
  // 未校准/同集号多版本仍然冲突：变体标记插入季集记号之后消歧
  const colliding = [
    task("Workplace Newcomers.2021.S05E01.2160p.WEB-DL.H264.AAC-UBWEB.mp4", "Part01"),
    task("Workplace Newcomers.2021.S05E01.2160p.WEB-DL.H264.AAC-UBWEB.mp4", "Part02")
  ];
  applyVariantTagsForCollisions(colliding);
  assert.equal(colliding[0].newName, "Workplace Newcomers.2021.S05E01.Part01.2160p.WEB-DL.H264.AAC-UBWEB.mp4");
  assert.equal(colliding[1].newName, "Workplace Newcomers.2021.S05E01.Part02.2160p.WEB-DL.H264.AAC-UBWEB.mp4");
  assert.ok(colliding[0].targetPath.endsWith(colliding[0].newName));
});

// —— TMDB 无数据时的候选兜底 ——
test("synthesizeEpisodeCandidatesFromNames 生成去重排序候选", () => {
  const files = [
    file("[x] 第01期 上.Show.2025.S05E01.Part01.2160p.mp4"),
    file("[x] 第01期 下.Show.2025.S05E01.Part02.2160p.mp4"),
    file("[x] 加更版 第01期.Show.2025.S05E01.2160p.mp4"),
    file("[x] 第02期.Show.2025.S05E02.2160p.mp4"),
    file("[x] 海报.jpg")
  ];
  const candidates = synthesizeEpisodeCandidatesFromNames(files);
  assert.deepEqual(Array.from(candidates.map((candidate) => candidate.seasonEpisode)), ["S05E01", "S05E02"]);
  assert.equal(candidates[0].id, "hint:S05E01");
});

// —— part / 第N部分 分段标记剔除 ——
test("标题里的 part/第N部分 标记全部剔除，不再进入新文件名", () => {
  assert.equal(inferTitle("药食同源 第1部分 part1.mp4"), "药食同源");
  assert.equal(inferTitle("药食同源 第1部分.mp4"), "药食同源");
  assert.equal(inferTitle("药食同源.Part1.mp4"), "药食同源");
  assert.equal(inferTitle("药食同源 Pt.2.mp4"), "药食同源");
  assert.equal(inferTitle("X part1 part2 1080p.mp4"), "X");
  assert.equal(inferTitle("回合 第一部分.mp4"), "回合");
  assert.equal(inferTitle("【药食同源 第1部分】第2期.mp4"), "药食同源");
  // 「第N部」是系列续作记号，不能误删
  assert.equal(inferTitle("流浪地球 第2部.mp4"), "流浪地球 第2部");
});

test("同集多 part 重名时仍由变体标签区分（Part01/Part02 保留）", () => {
  assert.equal(organizeVariantTag(file("药食同源 第1部分 part1.mp4"), {}), "Part01");
  assert.equal(organizeVariantTag(file("药食同源 第1部分 part2.mp4"), {}), "Part02");
  assert.equal(organizeVariantTag(file("药食同源 第1部分 上.mp4"), {}), "上");
});

test("同剧各 part 文件归入同一分组，分组标题不带分段标记", () => {
  const groups = buildLooseGroups([
    file("药食同源 第1部分 part1.mp4", "a1"),
    file("药食同源 第1部分 part2.mp4", "a2"),
    file("药食同源 第3期.mp4", "a3")
  ], config);
  assert.equal(groups.length, 1);
  assert.ok(!groups[0].title.includes("部分"), `分组标题不应残留分段标记：${groups[0].title}`);
});

await chain;
console.log(`\n${passed} 个用例全部通过`);

// —— 「集号+集名」形态：父目录名兜底分组（01 郭女侠怒砸同福店 佟掌柜秒点迷路人） ——
const formFile = (name, id, extra = {}) => ({ id, name, ...extra });

test("集名形态按父目录名并成一组，季集按前导数字解析", () => {
  const groups = buildLooseGroups([
    formFile("01 郭女侠怒砸同福店 佟掌柜秒点迷路人.mkv", "w1", { sourceFolderId: "f1", sourceFolderName: "武林外传", parentId: "900" }),
    formFile("02 五岳盟主之争.mp4", "w2", { sourceFolderId: "f1", sourceFolderName: "武林外传", parentId: "900" }),
    formFile("03 群雄争霸夺魁首.rmvb", "w3", { sourceFolderId: "f1", sourceFolderName: "武林外传", parentId: "900" })
  ], config);
  assert.equal(groups.length, 1, `应并成一组，实际 ${groups.length}`);
  assert.equal(groups[0].title, "武林外传");
  assert.equal(parseSeasonEpisode("01 郭女侠怒砸同福店 佟掌柜秒点迷路人.mkv").seasonEpisode, "S01E01");
  assert.equal(parseSeasonEpisode("03 群雄争霸夺魁首.rmvb").episode, 3);
});

test("直接勾选文件时用当前目录名兜底（options.currentDirName）", () => {
  const grouped = buildLooseGroups([
    formFile("01 郭女侠怒砸同福店.mkv", "b1", { parentId: "900" }),
    formFile("02 五岳盟主之争.mp4", "b2", { parentId: "900" })
  ], config, { currentDirName: "武林外传" });
  assert.equal(grouped.length, 1);
  assert.equal(grouped[0].title, "武林外传");
  // 没有目录名上下文时维持旧行为：逐文件一组
  const loose = buildLooseGroups([
    formFile("01 郭女侠怒砸同福店.mkv", "c1"),
    formFile("02 五岳盟主之争.mp4", "c2")
  ], config);
  assert.equal(loose.length, 2);
});

test("目录名带季号时剥出剧名并记目标季；纯季目录向上找剧名", () => {
  const seasonFolder = buildLooseGroups([
    formFile("01 归来.mp4", "d1", { sourceFolderId: "f2", sourceFolderName: "武林外传 第二季", parentId: "901" }),
    formFile("02 相逢.mp4", "d2", { sourceFolderId: "f2", sourceFolderName: "武林外传 第二季", parentId: "901" })
  ], config);
  assert.equal(seasonFolder.length, 1);
  assert.equal(seasonFolder[0].title, "武林外传");
  assert.equal(seasonFolder[0].targetSeason, 2);

  const nested = buildLooseGroups([
    { id: "n1", name: "07 风波.mp4", sourceFolderId: "s2", sourceFolderName: "Season 2", sourceFolders: [{ id: "f1", name: "武林外传", depth: 0 }, { id: "s2", name: "Season 2", depth: 1 }], parentId: "s2" },
    { id: "n2", name: "08 转机.mp4", sourceFolderId: "s2", sourceFolderName: "Season 2", sourceFolders: [{ id: "f1", name: "武林外传", depth: 0 }, { id: "s2", name: "Season 2", depth: 1 }], parentId: "s2" }
  ], config);
  assert.equal(nested.length, 1);
  assert.equal(nested[0].title, "武林外传");
  assert.equal(nested[0].targetSeason, 2);
});

test("带年份/画质标记或年份形前导数字的不算集名形态", () => {
  const groups = buildLooseGroups([
    formFile("01 武林外传 2006 1080p.mkv", "g1"),
    formFile("02 武林外传 2006 1080p.mkv", "g2"),
    formFile("2001 太空漫游.mkv", "g3")
  ], config, { currentDirName: "电影合集" });
  // 前两个是普通命名（含年份/画质）按各自标题识别；2001 开头是年份不是集数，都不并入「电影合集」
  for (const group of groups) {
    assert.notEqual(group.title, "电影合集");
  }
});

test("parseSeasonEpisode 集名形态边界", () => {
  assert.equal(parseSeasonEpisode("07 风波.mp4").seasonEpisode, "S01E07");
  assert.equal(parseSeasonEpisode("07-09 风波.mp4").endEpisode, 9);
  assert.equal(parseSeasonEpisode("21 Jump Street.mp4").episode, 0, "拉丁集名不启用前导集数");
  assert.equal(parseSeasonEpisode("2001 太空漫游.mp4").episode, 0, "年份形前导数字不是集数");
  assert.equal(parseSeasonEpisode("武林外传.S01E01.mkv").episode, 1, "常规 SxxEyy 不受影响");
});

// —— 带年份的续作目录：按目录分组，不再塌缩成第一部的分组（怪物史瑞克 1-4） ——
test("带年份的续作子目录各成一组，续作不再并进第一作", () => {
  const mk = (index, folder, name) => ({ id: `s${index}`, name, relativePath: `${folder}/${name}`, sourceFolderId: `d${index}`, sourceFolderName: folder, parentId: "900" });
  const frds = "BluRay.1080p.x265.10bit.3Audio.MNHD-FRDS";
  const groups = buildLooseGroups([
    mk(0, `怪物史瑞克.Shrek.2001.${frds}`, `Shrek.2001.${frds}.mkv`),
    mk(0, `怪物史瑞克.Shrek.2001.${frds}`, "cover.jpg"),
    mk(1, `怪物史瑞克2.Shrek.2.2004.${frds}`, `Shrek.2.2004.${frds}.mkv`),
    mk(1, `怪物史瑞克2.Shrek.2.2004.${frds}`, "cover.jpg"),
    mk(2, `怪物史瑞克3.Shrek.the.Third.2007.${frds}`, `Shrek.the.Third.2007.${frds}.mkv`),
    mk(3, `怪物史瑞克4.Shrek.Forever.After.2010.${frds}`, `Shrek.Forever.After.2010.${frds}.mkv`)
  ], config);
  assert.equal(groups.length, 4, `四部续作应各成一组，实际 ${groups.length} 组`);
  const titles = groups.map((group) => group.title).join("|");
  assert.ok(titles.includes("怪物史瑞克2"), `第二部应有自己的分组：${titles}`);
  assert.ok(titles.includes("怪物史瑞克3"), `第三部应有自己的分组：${titles}`);
});

test("无目录上下文时，带年份的续作文件名也不并入第一部", () => {
  const groups = buildLooseGroups([
    formFile("怪物史瑞克.Shrek.2001.BluRay.1080p.mkv", "m1"),
    formFile("怪物史瑞克2.Shrek.2.2004.BluRay.1080p.mkv", "m2")
  ], config);
  assert.equal(groups.length, 2, `年份不相交的续作不应并组，实际 ${groups.length} 组`);
});

test("文件名带年份时结尾紧贴中文的数字按续作保留（叶问2 ≠ 叶问）", () => {
  const groups = buildLooseGroups([
    formFile("叶问.2008.BluRay.mkv", "y1"),
    formFile("叶问2.2010.BluRay.mkv", "y2")
  ], config);
  assert.equal(groups.length, 2);
});
