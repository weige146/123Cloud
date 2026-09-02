// TMDB 季集校准回归测试：直接从 123-helper.user.js bundle 中切出识别模块，
// 在 Node 里驱动 matchEpisodeCandidates，覆盖「会员版/加更」关键字的匹配路径。
// 用法：node 油猴脚本/tests/tmdb-calibration.test.mjs
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
  slice("// src/core/categories.js", "// src/icons.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");
const driver = `;
globalThis.__recognition = { parseEpisodeHint, specialContext, matchEpisodeCandidates, filterEpisodeCandidatesForTargetSeason, reconcileCalibrationSeason, previewEpisodeCalibration, isVideoFile, extractKeywordKinds, applySpecialKeywordMappings, defaultSpecialKeywordPatterns, normalizeConfig, getSpecialKindTokens: () => SPECIAL_KIND_TOKENS };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { parseEpisodeHint, matchEpisodeCandidates, filterEpisodeCandidatesForTargetSeason, reconcileCalibrationSeason, previewEpisodeCalibration, extractKeywordKinds, applySpecialKeywordMappings, defaultSpecialKeywordPatterns, specialContext, normalizeConfig, getSpecialKindTokens } = sandbox.__recognition;

// —— TMDB《嗨放派》(id 131777) 真实季集数据（2026-08 从 themoviedb.org 核对）——
const ep = (season, episode, name, airDate) => ({ id: `s${season}e${episode}`, seasonNumber: season, episodeNumber: episode, name, airDate });
const SEASON_2 = [
  ep(2, 1, "很高兴认识我", "2022-08-06"),
  ep(2, 2, "给人工智能找BUG", "2022-08-13"),
  ep(2, 3, "自制剧特辑2.0", "2022-08-20"),
  ep(2, 4, "生活中的意想不到之力", "2022-08-27"),
  ep(2, 5, "寻找丢失的π币", "2022-09-03"),
  ep(2, 6, "大自然X档案", "2022-09-10"),
  ep(2, 7, "夏日型男速成记", "2022-09-17"),
  ep(2, 8, "嗨放奇遇记", "2022-09-24"),
  ep(2, 9, "一年一度“金鹊”大赛", "2022-11-05"),
  ep(2, 10, "换个角度看世界", "2022-11-12")
];
const SEASON_3 = [
  ep(3, 1, "「人算不如天算」", "2024-08-30"),
  ep(3, 2, "「放大镜」", "2024-09-06"),
  ep(3, 3, "「沉默的晚餐」", "2024-09-13")
];
const SPECIALS = [
  ep(0, 12, "第2季 第1期加更", "2022-08-10"),
  ep(0, 16, "第2季 第5期加更", "2022-09-05"),
  ep(0, 17, "第2季 第6期加更", "2022-09-12"),
  ep(0, 20, "第2季 第9期加更", "2022-11-07"),
  ep(0, 21, "第2季 第10期加更", "2022-11-14"),
  ep(0, 22, "第3季 先导片", "2024-08-23"),
  ep(0, 23, "第3季 第1期加更", "2024-09-02"),
  ep(0, 1, "第1季 先导片：什么是嗨放派？", "2021-08-14")
];
const TMDB_SEASONS = [
  { season_number: 0, air_date: "2021-08-14" },
  { season_number: 1, air_date: "2021-08-21" },
  { season_number: 2, air_date: "2022-08-06" },
  { season_number: 3, air_date: "2024-08-30" }
];
const TMDB_MEDIA = { id: 131777, mediaType: "tv", name: "嗨放派", year: "2021", seasons: TMDB_SEASONS };
const EPISODE_POOL = { 0: SPECIALS, 2: SEASON_2, 3: SEASON_3 };
const stubTmdb = {
  async details() { return TMDB_MEDIA; },
  async season(_id, season) { return EPISODE_POOL[season] || []; }
};
const group = (files, extraFields = {}) => ({ fields: { mediaType: "tv", season: "1", ...extraFields }, media: TMDB_MEDIA, files: files.map((name, index) => ({ id: String(index + 1), name })) });
const memberFile = "[20221107][嗨放派 第二季 会员版 第09期].Have.Fun.VIP.Version.2022.S01E09.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
const extraFile = "[20221114][嗨放派 第二季 加更版 第10期].Have.Fun.Extra.2022.S01E10.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
const regularFile = "[20220827][嗨放派 第二季 第04期].Have.Fun.2022.S01E04.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4";
const files = (names) => names.map((name, index) => ({ id: String(index + 1), name }));

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    await fn();
    passed += 1;
    console.log(`  ok ${passed} - ${title}`);
  });
};

test("会员版文件解析：期数/日期来自文件名，season 取自 SxxExx 记号", () => {
  const hint = parseEpisodeHint(memberFile, 2);
  assert.equal(hint.season, 1);
  assert.equal(hint.episode, 9);
  assert.equal(hint.date, "2022-11-07");
});

test("会员版文件在无 S00 的第 2 季池中映射到同期正集（会员版=同集加长）", () => {
  const matches = matchEpisodeCandidates(files([memberFile]), SEASON_2, 1, 2);
  const match = matches.get("1");
  assert.ok(match, "会员版第09期应被校准");
  assert.equal(match.seasonNumber, 2);
  assert.equal(match.episodeNumber, 9);
  assert.equal(match.reason, "TMDB 会员版 变体期数映射");
});

test("加更版不落正季集：TMDB 无 S00 时保持未校准", () => {
  const matches = matchEpisodeCandidates(files([extraFile]), SEASON_2, 1, 2);
  assert.equal(matches.get("1"), undefined, "加更版是独立加集，不得映射到同期正集");
});

test("加更版 S03E01 优先命中 TMDB S00 加更条目，而不是撞号的正集 S03E01", () => {
  const file = "[20250901][嗨放派 第三季 加更版 第01期].Have.Fun.Extra.2025.S03E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  const pool = [ep(3, 1, "第一期正片", "2025-08-30"), ep(3, 2, "第二期正片", "2025-09-06"), ep(0, 1, "加更版第1期", "2025-09-02")];
  const matches = matchEpisodeCandidates(files([file]), pool, 1, 3);
  const match = matches.get("1");
  assert.ok(match, "加更版应命中 S00");
  assert.equal(match.seasonNumber, 0);
  assert.equal(match.episodeNumber, 1);
  assert.notEqual(match.reason, "TMDB 文件名季集匹配");
});

test("先导片带 S03E01 记号时命中 TMDB S00 先导片条目（关键词一致即可）", () => {
  const file = "[20250828][嗨放派 第三季 先导片].Have.Fun.Preview.2025.S03E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  const pool = [ep(3, 1, "第一期正片", "2025-08-30"), ep(0, 1, "先导片", "2025-08-28")];
  const matches = matchEpisodeCandidates(files([file]), pool, 1, 3);
  const match = matches.get("1");
  assert.ok(match, "先导片应命中 S00");
  assert.equal(match.seasonNumber, 0);
  assert.equal(match.episodeNumber, 1);
});

test("先导片在 TMDB 无对应条目时保持未校准（不虚构映射）", () => {
  const file = "[20250828][嗨放派 第三季 先导片].Have.Fun.Preview.2025.S03E00.2160p.WEB-DL.H265.AAC-UBWEB.mp4";
  const matches = matchEpisodeCandidates(files([file]), [ep(3, 1, "第一期正片", "2025-08-30")], 1, 3);
  assert.equal(matches.get("1"), undefined);
});

test("普通文件记号权威：S03E04 直接命中第 3 季 E04（不回归）", () => {
  const file = "[20250920][嗨放派 第三季 第04期].Have.Fun.2025.S03E04.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4";
  const pool = [ep(3, 1, "第一期正片", "2025-08-30"), ep(3, 4, "第四期正片", "2025-09-20")];
  const matches = matchEpisodeCandidates(files([file]), pool, 1, 3);
  const match = matches.get("1");
  assert.ok(match);
  assert.equal(match.seasonNumber, 3);
  assert.equal(match.episodeNumber, 4);
  assert.equal(match.reason, "TMDB 文件名季集匹配");
});

test("TMDB 收录 S00 会员版条目时优先匹配 S00（期数一致）", () => {
  const pool = [...SEASON_2, ep(0, 9, "会员版第9期", "2022-11-07"), ep(0, 10, "会员版第10期", "2022-11-14")];
  const matches = matchEpisodeCandidates(files([memberFile, extraFile]), pool, 1, 2);
  const member = matches.get("1");
  assert.ok(member, "会员版第09期应命中 S00");
  assert.equal(member.seasonNumber, 0);
  assert.equal(member.episodeNumber, 9);
  const extra = matches.get("2");
  assert.ok(extra, "加更版第10期应命中 S00");
  assert.equal(extra.seasonNumber, 0);
  assert.equal(extra.episodeNumber, 10);
});

test("普通文件沿用播出日期匹配，行为不变", () => {
  const matches = matchEpisodeCandidates(files([regularFile]), SEASON_2, 1, 2);
  const match = matches.get("1");
  assert.ok(match, "第04期应按播出日期校准");
  assert.equal(match.seasonNumber, 2);
  assert.equal(match.episodeNumber, 4);
  assert.ok(match.reason.includes("2022-08-27"));
});

test("无关键字且 TMDB 无对应集时保持不解析（回归保护）", () => {
  const matches = matchEpisodeCandidates(files(["Show.2022.S01E99.2160p.WEB-DL.mp4"]), SEASON_2, 1, 2);
  assert.equal(matches.get("1"), undefined);
});

test("带记号的会员版文件不误配无关的孤立 S00 条目（守卫生效）", () => {
  const unrelated = ep(0, 1, "幕后特辑", "2022-10-01");
  const matches = matchEpisodeCandidates(files([memberFile]), [...SEASON_2, unrelated], 1, 2);
  const match = matches.get("1");
  assert.ok(match, "应走变体期数映射而不是错配 S00");
  assert.equal(match.seasonNumber, 2);
  assert.equal(match.episodeNumber, 9);
});

test("关键词相同但期数/日期都不符的 S00 不被采纳（守卫生效）", () => {
  const weak = ep(0, 2, "会员专属幕后", "2022-10-01");
  const matches = matchEpisodeCandidates(files([memberFile]), [...SEASON_2, weak], 1, 2);
  const match = matches.get("1");
  assert.ok(match);
  assert.equal(match.seasonNumber, 2);
  assert.equal(match.episodeNumber, 9);
});

test("候选池过滤：会员版关键字的文件会把相关 S00 条目带进第 2 季池", () => {
  const pool = filterEpisodeCandidatesForTargetSeason(
    [...SEASON_2, ep(0, 9, "会员版第9期", "2022-11-07"), ep(0, 99, "其他季加更", "2021-01-01")],
    2,
    [memberFile]
  );
  assert.ok(pool.some((episode) => episode.seasonNumber === 0 && episode.episodeNumber === 9), "S00 会员版第9期应保留");
  assert.ok(!pool.some((episode) => episode.episodeNumber === 99), "无关 S00 应被过滤");
});

test("整组场景：截图中的 9 个文件（普通 + 会员版混合）全部校准", () => {
  const group = files([
    "[20221107][嗨放派 第二季 会员版 第09期].Have.Fun.VIP.Version.2022.S01E09.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20220912][嗨放派 第二季 会员版 第06期].Have.Fun.VIP.Version.2022.S01E06.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20221114][嗨放派 第二季 会员版 第10期].Have.Fun.VIP.Version.2022.S01E10.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20220827][嗨放派 第二季 第04期].Have.Fun.2022.S01E04.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220813][嗨放派 第二季 第02期].Have.Fun.2022.S01E02.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20221112][嗨放派 第二季 第10期].Have.Fun.2022.S01E10.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20221105][嗨放派 第二季 第09期].Have.Fun.2022.S01E09.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220806][嗨放派 第二季 第01期].Have.Fun.2022.S01E01.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220905][嗨放派 第二季 会员版 第05期].Have.Fun.VIP.Version.2022.S01E05.2160p.WEB-DL.H265.AAC-UBWEB.mp4"
  ]);
  const matches = matchEpisodeCandidates(group, SEASON_2, 1, 2);
  for (const file of group) {
    const match = matches.get(file.id);
    assert.ok(match, `未校准：${file.name}`);
    assert.equal(match.seasonNumber, 2, `季错误：${file.name}`);
  }
  const episodeOf = (file) => matches.get(file.id).episodeNumber;
  assert.deepEqual([episodeOf(group[0]), episodeOf(group[1]), episodeOf(group[2]), episodeOf(group[8])], [9, 6, 10, 5], "会员版应映射到同期正集");
  assert.deepEqual([episodeOf(group[3]), episodeOf(group[4]), episodeOf(group[5]), episodeOf(group[6]), episodeOf(group[7])], [4, 2, 10, 9, 1], "普通文件按播出日期校准");
  assert.equal(episodeOf(group[2]), episodeOf(group[5]), "会员版第10期与普通第10期落同一集");
});

test("季对齐：S01 记号 + 片名“第二季” + 2022 年份 → 目标季重映射 1→2", () => {
  const remap = reconcileCalibrationSeason(group([
    "[20220806][嗨放派 第二季 第01期].Have.Fun.2022.S01E01.2160p.WEB-DL.mp4",
    "[20221107][嗨放派 第二季 会员版 第09期].Have.Fun.VIP.Version.2022.S01E09.2160p.WEB-DL.mp4"
  ]), TMDB_MEDIA);
  assert.ok(remap, "应产生季重映射");
  assert.equal(remap.from, 1);
  assert.equal(remap.to, 2);
});

test("季对齐：记号与片名季一致时不重映射", () => {
  const remap = reconcileCalibrationSeason(group([
    "[20220806][嗨放派 第二季 第01期].Have.Fun.2022.S02E01.2160p.WEB-DL.mp4"
  ]), TMDB_MEDIA);
  assert.equal(remap, null);
});

test("季对齐：文件年份与 TMDB 目标季差距过大时拒绝重映射", () => {
  const remap = reconcileCalibrationSeason(group([
    "[20190101][某剧 第二季 第01期].Show.2019.S01E01.2160p.WEB-DL.mp4"
  ]), TMDB_MEDIA);
  assert.equal(remap, null);
});

test("端到端：截图整组（S01 记号 + 第二季）自动对齐第 2 季，正片+会员版全部正确", async () => {
  const g = group([
    "[20220806][嗨放派 第二季 第01期].Have.Fun.2022.S01E01.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220813][嗨放派 第二季 第02期].Have.Fun.2022.S01E02.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220827][嗨放派 第二季 第04期].Have.Fun.2022.S01E04.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20221105][嗨放派 第二季 第09期].Have.Fun.2022.S01E09.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20221112][嗨放派 第二季 第10期].Have.Fun.2022.S01E10.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20220809][嗨放派 第二季 会员版 第01期].Have.Fun.VIP.Version.2022.S01E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20220905][嗨放派 第二季 会员版 第05期].Have.Fun.VIP.Version.2022.S01E05.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20220912][嗨放派 第二季 会员版 第06期].Have.Fun.VIP.Version.2022.S01E06.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20221107][嗨放派 第二季 会员版 第09期].Have.Fun.VIP.Version.2022.S01E09.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20221114][嗨放派 第二季 会员版 第10期].Have.Fun.VIP.Version.2022.S01E10.2160p.WEB-DL.H265.AAC-UBWEB.mp4"
  ]);
  const result = await previewEpisodeCalibration(stubTmdb, g);
  assert.equal(result.warnings.length, 0);
  const matches = result.matches;
  const of = (index) => matches[String(index + 1)];
  for (let index = 0; index < 10; index++) assert.ok(of(index), `文件未校准：${g.files[index].name}`);
  const expect = [[2, 1], [2, 2], [2, 4], [2, 9], [2, 10]];
  expect.forEach(([season, episode], index) => {
    assert.equal(of(index).seasonNumber, season, `正片季错误：${g.files[index].name}`);
    assert.equal(of(index).episodeNumber, episode, `正集号错误：${g.files[index].name}`);
  });
  const memberExpect = [[0, 12], [0, 16], [0, 17], [0, 20], [0, 21]];
  memberExpect.forEach(([season, episode], offset) => {
    const index = 5 + offset;
    assert.equal(of(index).seasonNumber, season, `会员版应命中 S00：${g.files[index].name}`);
    assert.equal(of(index).episodeNumber, episode, `会员版集号错误：${g.files[index].name}`);
  });
  assert.ok(of(8).name.includes("第2季 第9期加更"), "会员版第09期应命中「第2季 第9期加更」");
});

test("端到端：第三季 加更 S03E01 → S00「第3季 第1期加更」，先导片 → S00「第3季 先导片」", async () => {
  const g = group([
    "[20240823][嗨放派 第三季 先导片].Have.Fun.Preview.2024.S03E00.2160p.WEB-DL.H265.AAC-UBWEB.mp4",
    "[20240830][嗨放派 第三季 第01期].Have.Fun.2024.S03E01.2160p.WEB-DL.HQ.H265.10bit.AAC-UBWEB.mp4",
    "[20240902][嗨放派 第三季 加更版 第01期].Have.Fun.Extra.2024.S03E01.2160p.WEB-DL.H265.AAC-UBWEB.mp4"
  ], { season: "3" });
  const result = await previewEpisodeCalibration(stubTmdb, g);
  const matches = result.matches;
  const pilot = matches["1"];
  assert.ok(pilot, "先导片应命中 S00");
  assert.equal(pilot.seasonNumber, 0);
  assert.equal(pilot.episodeNumber, 22);
  assert.ok(pilot.name.includes("先导片"));
  const regular = matches["2"];
  assert.ok(regular, "正片应命中第 3 季");
  assert.equal(regular.seasonNumber, 3);
  assert.equal(regular.episodeNumber, 1);
  const extra = matches["3"];
  assert.ok(extra, "加更版第01期应命中 S00");
  assert.equal(extra.seasonNumber, 0);
  assert.equal(extra.episodeNumber, 23);
  assert.ok(extra.name.includes("第3季 第1期加更"), "加更版应命中「第3季 第1期加更」而不是正集 S03E01");
});

// —— 特别篇关键词映射配置化：默认词表与旧硬编码正则等价，设置项可扩展 ——
const LEGACY_SPECIAL_KEYWORD_PATTERNS = [
  ["先导片", /先导(?:片|篇)?|导赏|导览|先行(?:片|篇)?|序篇|序章|预热|尝鲜|抢先(?:看|版)?|抢鲜(?:看|版)?|预告|片花|Trailer|Teaser|Preview|Sneak\s*Peek|Promo|(?<![A-Za-z0-9])PV(?![A-Za-z0-9])/i],
  ["番外衍生", /番外(?:篇|特辑|微综)?|衍生(?:篇|节目)?|Side[\s._-]*Story|Spin[\s._-]*Off|Spinoff/i],
  ["加更", /加更(?:篇|版)?|加料(?:版)?|独家加更|(?<![A-Za-z0-9])(?:Plus|Extra)(?![A-Za-z0-9])/i],
  ["彩蛋福利", /福利局|惊喜局|彩蛋(?:局)?|会员\s*彩蛋|VIP[\s._-]*Bonus(?:[\s._-]*Scene)?|(?<![A-Za-z0-9])Bonus(?![A-Za-z0-9])/i],
  ["超前企划", /超前(?:营业|聚会|企划)|First[\s._-]*Look/i],
  ["会员专享", /会员(?:版|加长|专享)|大会员|VIP(?:版|专享)?|SVIP|专享版|独享版|Members?\s*Only/i],
  ["幕后花絮", /幕后(?:纪录|特辑|直击)?|花絮|制作特辑|探班|备采|采访|彩排|Behind(?:\s+the)?\s*Scenes?|Making\s*Of/i],
  ["纯享直拍", /纯享|舞台纯享|歌曲纯享|完整纯享|单人直拍|多机位|练习室版|Fancam|Fan\s*Cam|Focus/i],
  ["直播演出", /直播(?:回放)?|演唱会|见面会|发布会|(?<![A-Za-z0-9])Live(?:\s*(?:Show|Stream))?(?![A-Za-z0-9])/i],
  ["陪看复盘", /陪看|聊天室|看片会|复盘|Reaction|Watch\s*Along/i],
  ["日记Vlog", /PD\s*Vlog|Vlog|日记|手记/i],
  ["收官重聚", /收官(?:篇|宴|特辑)?|庆功宴|重聚|售后|After\s*Show|Aftershow|After\s*Party|Afterparty|Reunion/i],
  ["回顾前情", /回顾|前情提要|(?<![A-Za-z0-9])(?:Recap|Digest)(?![A-Za-z0-9])/i],
  ["特辑", /特辑|特别(?:篇|节目|企划)?|特别\s*企划|Special[\s._-]*Program|(?<![A-Za-z0-9])(?:SP|Special|OVA|OAD)(?![A-Za-z0-9])/i],
  ["好友记", /好友记|Old[\s._-]*Friends?/i],
  ["加码放送", /加码放送|Special[\s._-]*Extra/i],
  ["未播删减", /未播|未公开|正片未播|删减片段|Deleted\s*Scene|Outtake|Bloopers?/i]
];
test("特别篇关键词：默认词表与旧硬编码正则在样本库上完全等价", () => {
  const corpus = [
    "第2季 第1期加更", "会员版 第3期", "先导片", "超前营业", "纯享 舞台", "单人直拍",
    "演唱会直播回放", "幕后花絮", "番外篇", "收官特辑", "前情提要", "第1期 Reaction",
    "成员日记 Vlog", "好友记", "加码放送", "未播删减片段", "彩蛋局", "特别节目",
    "Sneak Peek", "Making Of", "Watch Along", "会员 彩蛋", "VIP Bonus Scene", "PD Vlog",
    "After Party", "Spin-Off", "First Look", "Members Only", "Deleted Scenes", "Bloopers",
    "Special Program", "第3期PV", "抢先看", "练习室版", "见面会", "手记", "Old Friends",
    "第3期正片", "完整版第5期", "第12期", "嗨放派 2024", "S03E01"
  ];
  const current = defaultSpecialKeywordPatterns();
  assert.equal(current.length, LEGACY_SPECIAL_KEYWORD_PATTERNS.length);
  for (const name of corpus) {
    assert.deepEqual(
      Array.from(extractKeywordKinds(name, current)).sort(),
      Array.from(extractKeywordKinds(name, LEGACY_SPECIAL_KEYWORD_PATTERNS)).sort(),
      `关键词识别不一致：${name}`
    );
  }
});

test("特别篇关键词：类型名可自定义（如「动脑吧」），新别名立即生效", () => {
  applySpecialKeywordMappings([
    { id: "video-format-1080p", field: "videoFormat", aliases: ["1080p"], output: "1080p" },
    { id: "special-pilot", field: "specialKind", aliases: ["先导", "预告"], output: "先导片" },
    { id: "special-extra", field: "specialKind", aliases: ["加更", "加餐"], output: "加更" },
    { id: "custom-derivative", field: "specialKind", aliases: ["动脑吧", "体验版", "开推吧! X"], output: "动脑吧" }
  ]);
  assert.ok(specialContext("第3期加餐").strongKeywords.includes("加更"), "新别名「加餐」应识别为加更类特别篇");
  assert.ok(specialContext("开始推理吧 第二季 动脑吧 第09期").strongKeywords.includes("动脑吧"), "自定义类型「动脑吧」应生效");
  assert.ok(specialContext("第二季 体验版 第01期").strongKeywords.includes("动脑吧"), "同类型下的词共享一个标记");
  assert.ok(!specialContext("第2期特辑").strong, "未配置的类型不再按强关键词识别（完全按配置）");
  applySpecialKeywordMappings(undefined);
  assert.ok(specialContext("第3期加更").strongKeywords.includes("加更"), "恢复默认后加更重新生效");
  assert.ok(specialContext("第2期特辑").strongKeywords.includes("特辑"), "恢复默认后 17 类词表齐全");
  applySpecialKeywordMappings([{ id: "special-pilot", field: "specialKind", aliases: ["先导"], output: "先锋场" }]);
  assert.equal(getSpecialKindTokens().pilot, "先锋场", "先导片专属行为应跟随改名后的类型名");
  applySpecialKeywordMappings(undefined);
  assert.equal(getSpecialKindTokens().pilot, "先导片", "恢复默认后行为标记复位");
});

test("特别篇关键词：老配置自动补齐词表，旧英文类型名改写为类型名", () => {
  const migrated = normalizeConfig({ schemaVersion: 10, library: { recognition: { customWords: [], fixedMappings: [{ id: "video-format-1080p", field: "videoFormat", aliases: ["1080p"], output: "1080p" }] } } });
  const mappings = Array.from(migrated.library.recognition.fixedMappings);
  assert.equal(migrated.schemaVersion, 11);
  assert.ok(mappings.some((item) => item.id === "video-format-1080p"), "既有映射保留");
  for (const kind of ["先导片", "加更", "会员专享", "特辑", "未播删减"]) {
    assert.ok(mappings.some((item) => item.field === "specialKind" && item.output === kind), `缺失的特别篇默认条目应自动补齐：${kind}`);
  }
  assert.ok(specialContext("第1期加更").strongKeywords.includes("加更"), "迁移后默认词表在当前会话立即生效");
  const rewritten = normalizeConfig({ schemaVersion: 11, library: { recognition: { customWords: [], fixedMappings: [
    { id: "video-format-1080p", field: "videoFormat", aliases: ["1080p"], output: "1080p" },
    { id: "special-pilot", field: "specialKind", aliases: ["先导"], output: "pilot" }
  ] } } });
  const pilot = Array.from(rewritten.library.recognition.fixedMappings).find((item) => item.id === "special-pilot");
  assert.ok(pilot, "用户编辑过的特别篇条目保留");
  assert.equal(pilot.output, "先导片", "旧英文类型名应改写为类型名");
  assert.ok(!Array.from(rewritten.library.recognition.fixedMappings).some((item) => item.field === "specialKind" && item.output === "加更"), "已留有特别篇条目时不再强制补齐（尊重用户编辑）");
});

await chain;
console.log(`\n全部 ${passed} 个用例通过`);
