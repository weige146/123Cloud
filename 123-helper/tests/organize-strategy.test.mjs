// 整理策略回归测试：覆盖 filmix 式「切换策略」的四种 TV 策略（单季顺序拆多季、
// 单季跳序拆多季、多季合并一季、TMDB 剧集组）以及电影合集归档的目标路径计算。
// 用法：node 油猴脚本/123-helper/tests/organize-strategy.test.mjs
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
globalThis.__strategy = {
  parseSeasonEpisode,
  isVideoFile,
  DEFAULT_CONFIG,
  ORGANIZE_STRATEGY_KINDS,
  organizeStrategyLabel,
  organizeStrategyActive,
  tmdbSeasonCapacities,
  normalizeEpisodeGroupDetail,
  flattenEpisodeGroupTargets,
  computeGroupStrategyAssignments,
  prepareMergeStrategyAssignments,
  refreshOrganizeGroupTargets,
  kindIsSplitStrategy,
  tmdbStructureFromMedia,
  splitSeasonBounds,
  strategyStructureCacheKey,
  resolveStrategyStructure,
  structureIsSingleSeason,
  structureSeasonChipsText,
  parseCureTmdbTvJson,
  structureFromCureTmdbEntry,
  matchBangumiSubject,
  bangumiSequelCandidates,
  buildBangumiChainSubjects,
  structureFromBangumiChain
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException, structuredClone };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const {
  parseSeasonEpisode,
  isVideoFile,
  DEFAULT_CONFIG,
  ORGANIZE_STRATEGY_KINDS,
  organizeStrategyLabel,
  organizeStrategyActive,
  tmdbSeasonCapacities,
  normalizeEpisodeGroupDetail,
  flattenEpisodeGroupTargets,
  computeGroupStrategyAssignments,
  prepareMergeStrategyAssignments,
  refreshOrganizeGroupTargets,
  kindIsSplitStrategy,
  tmdbStructureFromMedia,
  splitSeasonBounds,
  strategyStructureCacheKey,
  resolveStrategyStructure,
  structureIsSingleSeason,
  structureSeasonChipsText,
  parseCureTmdbTvJson,
  structureFromCureTmdbEntry,
  matchBangumiSubject,
  bangumiSequelCandidates,
  buildBangumiChainSubjects,
  structureFromBangumiChain
} = sandbox.__strategy;

const file = (id, name, extra = {}) => ({ id: String(id), name, ...extra });
// vm 沙箱产物与主沙箱原型不同，deepEqual 前先转成普通对象
const plain = (value) => JSON.parse(JSON.stringify(value));

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    try {
      await fn();
      passed += 1;
      console.log(`  \u001b[32m✓\u001b[0m ${title}`);
    } catch (error) {
      console.error(`  \u001b[31m✗\u001b[0m ${title}`);
      throw error;
    }
  });
};

const seasonOf = (assignments, fileId) => assignments.get(String(fileId));
const labels = (assignments) => [...assignments.entries()].map(([id, match]) => `${id}:${match.seasonEpisode}`).sort();

test("策略元数据：五种策略与中文标签", () => {
  assert.equal(ORGANIZE_STRATEGY_KINDS.length, 5);
  assert.deepEqual([...ORGANIZE_STRATEGY_KINDS.map((item) => item.kind)], ["default", "splitSequential", "splitKeep", "merge", "episodeGroup"]);
  assert.equal(organizeStrategyLabel(null), "默认策略");
  assert.equal(organizeStrategyLabel({ kind: "default" }), "默认策略");
  assert.equal(organizeStrategyLabel({ kind: "splitSequential" }), "单季顺序拆多季");
  assert.equal(organizeStrategyLabel({ kind: "episodeGroup", episodeGroupName: "绝对顺序" }), "剧集组：绝对顺序");
  assert.equal(organizeStrategyActive({ kind: "default" }), false);
  assert.equal(organizeStrategyActive({ kind: "merge" }), true);
});

test("tmdbSeasonCapacities：只取正片季且过滤非法集数", () => {
  const capacities = tmdbSeasonCapacities({ seasons: [
    { season_number: 0, episode_count: 5 },
    { season_number: 2, episode_count: 16 },
    { season_number: 1, episode_count: 61 },
    { season_number: 3, episode_count: 0 },
    { season_number: 4 }
  ] });
  assert.deepEqual(plain(capacities), [{ season: 1, count: 61 }, { season: 2, count: 16 }]);
});

test("拆分策略·单季顺序拆多季：每季从第 1 集重排，超出 TMDB 总集数顺延到最后一季", () => {
  const media = { seasons: [{ season_number: 1, episode_count: 20 }, { season_number: 2, episode_count: 18 }] };
  const files = [];
  for (let episode = 1; episode <= 50; episode += 1) files.push(file(episode, `龙珠Z S01E${String(episode).padStart(3, "0")}.mkv`));
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, media, { fallbackSeason: 1 });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 20).seasonEpisode, "S01E20");
  assert.equal(seasonOf(result.assignments, 21).seasonEpisode, "S02E01");
  assert.equal(seasonOf(result.assignments, 38).seasonEpisode, "S02E18");
  // 超出总量（38 集）的文件顺延到最后一季继续编号
  assert.equal(seasonOf(result.assignments, 39).seasonEpisode, "S02E39");
  assert.equal(seasonOf(result.assignments, 50).seasonEpisode, "S02E50");
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0], /超出总集数/);
});

test("拆分策略·单季顺序拆多季：多集合并文件按跨度占位", () => {
  const media = { seasons: [{ season_number: 1, episode_count: 3 }, { season_number: 2, episode_count: 2 }] };
  const files = [file(1, "剧 S01E01-E02.mkv"), file(2, "剧 S01E03.mkv"), file(3, "剧 S01E04.mkv"), file(4, "剧 S01E05.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, media, { fallbackSeason: 1 });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01-E02");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E03");
  assert.equal(seasonOf(result.assignments, 3).seasonEpisode, "S02E01");
  assert.equal(seasonOf(result.assignments, 4).seasonEpisode, "S02E02");
  assert.deepEqual(plain(result.warnings), []);
});

test("拆分策略·单季跳序拆多季：只改季号不改集号，特别篇保持不动", () => {
  const media = { seasons: [{ season_number: 1, episode_count: 61 }, { season_number: 2, episode_count: 16 }, { season_number: 3, episode_count: 42 }] };
  const files = [
    file(1, "海贼王 E001.mkv"),
    file(61, "海贼王 E061.mkv"),
    file(62, "海贼王 E062.mkv"),
    file(77, "海贼王 E077.mkv"),
    file(78, "海贼王 E078.mkv"),
    file(100, "海贼王 E100.mkv"),
    file(200, "海贼王 E200.mkv"),
    file(900, "海贼王 S00E12 特别篇.mkv")
  ];
  const result = computeGroupStrategyAssignments({ kind: "splitKeep" }, files, media, { fallbackSeason: 1 });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 61).seasonEpisode, "S01E61");
  assert.equal(seasonOf(result.assignments, 62).seasonEpisode, "S02E62");
  assert.equal(seasonOf(result.assignments, 77).seasonEpisode, "S02E77");
  assert.equal(seasonOf(result.assignments, 78).seasonEpisode, "S03E78");
  assert.equal(seasonOf(result.assignments, 100).seasonEpisode, "S03E100");
  // 超出总集数（119 集）保留在最后一季
  assert.equal(seasonOf(result.assignments, 200).seasonEpisode, "S03E200");
  // 显式 S00 特别篇不参与拆分
  assert.equal(result.assignments.get("900"), undefined);
  assert.ok(result.warnings.some((message) => message.includes("超出总集数")));
});

test("合并策略·多季合并一季：集号按顺序累加，特别篇不参与", () => {
  const files = [
    file(1, "柯南 S01E01.mkv"),
    file(2, "柯南 S01E02.mkv"),
    file(3, "柯南 S02E01.mkv"),
    file(4, "柯南 S02E02.mkv"),
    file(5, "柯南 S02E03-E04.mkv"),
    file(6, "柯南 S00E05 特别篇.mkv")
  ];
  const result = computeGroupStrategyAssignments({ kind: "merge" }, files, null, { fallbackSeason: 1 });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E02");
  assert.equal(seasonOf(result.assignments, 3).seasonEpisode, "S01E03");
  assert.equal(seasonOf(result.assignments, 4).seasonEpisode, "S01E04");
  assert.equal(seasonOf(result.assignments, 5).seasonEpisode, "S01E05-E06");
  assert.equal(result.assignments.get("6"), undefined);
});

test("合并策略·跨同名分组：prepareMergeStrategyAssignments 为每个参与分组产出连续编号", () => {
  const groupA = { id: "A", title: "柯南", fields: { mediaType: "tv", title: "柯南", season: "1" }, media: null, files: [file("a1", "柯南 S01E01.mkv"), file("a2", "柯南 S01E02.mkv")] };
  const groupB = { id: "B", title: "柯南", fields: { mediaType: "tv", title: "柯南", season: "2" }, media: null, files: [file("b1", "柯南 S02E01.mkv"), file("b2", "柯南 S02E02.mkv")] };
  const mergeScopes = { A: [{ groupId: "A", fileIds: ["a1", "a2"] }, { groupId: "B", fileIds: ["b1", "b2"] }] };
  const assignments = prepareMergeStrategyAssignments([groupA, groupB], { A: { kind: "merge" } }, mergeScopes);
  // 每个分组的分配表只含自己的文件；跨组连续性体现在 B 组从 E03 接续
  assert.deepEqual(labels(assignments.A), ["a1:S01E01", "a2:S01E02"]);
  assert.deepEqual(labels(assignments.B), ["b1:S01E03", "b2:S01E04"]);
});

test("合并策略·真实结构对号入座：SxxEyy 折算成全作累计集数（狐妖小红娘式）", () => {
  const structure = structureFromBangumiChain([{ name_cn: "第1篇", eps: 13 }, { name_cn: "第2篇", eps: 14 }, { name_cn: "第3篇", eps: 21 }]);
  const files = [
    file(1, "狐妖小红娘 S01E05.mkv"),
    file(2, "狐妖小红娘 S02E01.mkv"),
    file(3, "狐妖小红娘 S02E02-E03.mkv"),
    file(4, "狐妖小红娘 S03E03.mkv")
  ];
  const result = computeGroupStrategyAssignments({ kind: "merge", structureSource: "bangumi" }, files, null, { fallbackSeason: 1, structure });
  // bounds: S1=1-13, S2=14-27, S3=28-48 → 目标单季 S01，集号=累计集数
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E05");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E14");
  assert.equal(seasonOf(result.assignments, 3).seasonEpisode, "S01E15-E16");
  assert.equal(seasonOf(result.assignments, 4).seasonEpisode, "S01E30");
  assert.equal(seasonOf(result.assignments, 4).seasonNumber, 1);
});

test("合并策略·真实结构：只整理其中一季也能算对偏移（偏移来自结构不依赖其他季在场）", () => {
  const structure = structureFromBangumiChain([{ name_cn: "第1篇", eps: 13 }, { name_cn: "第2篇", eps: 14 }, { name_cn: "第3篇", eps: 21 }]);
  const files = [file(1, "狐妖小红娘 S03E01.mkv"), file(2, "狐妖小红娘 S03E02.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "merge", structureSource: "bangumi" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E28");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E29");
  assert.deepEqual(plain(result.warnings), []);
});

test("合并策略·真实结构：单期结构集号当全作累计集数（柯南式），季号超出结构顺序排尾并警示", () => {
  const single = structureFromBangumiChain([{ name_cn: "名侦探柯南", eps: 1100 }]);
  const singleFiles = [file(1, "名侦探柯南 E0500.mkv"), file(2, "名侦探柯南 S02E03.mkv")];
  const singleResult = computeGroupStrategyAssignments({ kind: "merge", structureSource: "bangumi" }, singleFiles, null, { fallbackSeason: 1, structure: single });
  assert.equal(seasonOf(singleResult.assignments, 1).seasonEpisode, "S01E500");
  // 单期结构里季 token 无意义 → 集号按绝对集数落位
  assert.equal(seasonOf(singleResult.assignments, 2).seasonEpisode, "S01E03");

  const multi = structureFromBangumiChain([{ name_cn: "第1篇", eps: 13 }, { name_cn: "第2篇", eps: 14 }]);
  const multiFiles = [file(1, "某番 S01E02.mkv"), file(2, "某番 S09E01.mkv")];
  const multiResult = computeGroupStrategyAssignments({ kind: "merge", structureSource: "bangumi" }, multiFiles, null, { fallbackSeason: 1, structure: multi });
  assert.equal(seasonOf(multiResult.assignments, 1).seasonEpisode, "S01E02");
  // S09 不在结构里 → 顺序排到已入座位置之后
  assert.equal(seasonOf(multiResult.assignments, 2).seasonEpisode, "S01E03");
  assert.ok(multiResult.warnings.some((message) => message.includes("季号不在")));
});

test("剧集组策略：normalize 按子组顺序映射目标季/集", () => {
  const detail = normalizeEpisodeGroupDetail({
    id: "gid1",
    name: "绝对顺序",
    type: 2,
    groups: [
      { id: "g2", name: "第2部分", order: 2, episodes: [
        { id: "e3", order: 1, season_number: 2, episode_number: 30, name: "第三集", air_date: "2020-01-03" },
        { id: "e4", order: 2, season_number: 2, episode_number: 31, name: "第四集", air_date: "2020-01-10" }
      ] },
      { id: "g1", name: "第1部分", order: 1, episodes: [
        { id: "e1", order: 2, season_number: 1, episode_number: 2, name: "第二集", air_date: "2020-01-02" },
        { id: "e2", order: 1, season_number: 1, episode_number: 1, name: "第一集", air_date: "2020-01-01" }
      ] }
    ]
  });
  assert.equal(detail.groups.length, 2);
  assert.deepEqual([...flattenEpisodeGroupTargets(detail).map((episode) => `${episode.targetSeason}x${episode.targetEpisode}`)], ["1x1", "1x2", "2x1", "2x2"]);
  assert.deepEqual([...flattenEpisodeGroupTargets(detail).map((episode) => episode.originEpisodeNumber)], [1, 2, 30, 31]);
});

test("剧集组策略：按原始季集、绝对集号、播出日期逐级匹配", () => {
  const detail = normalizeEpisodeGroupDetail({
    id: "gid1",
    groups: [
      { id: "g1", name: "S1", order: 1, episodes: [{ id: "e1", order: 1, season_number: 1, episode_number: 1, name: "第一集", air_date: "2020-01-01" }] },
      { id: "g2", name: "S2", order: 2, episodes: [
        { id: "e30", order: 1, season_number: 2, episode_number: 30, name: "第三十集", air_date: "2020-01-30" },
        { id: "e31", order: 2, season_number: 2, episode_number: 31, name: "第三十一集", air_date: "2020-01-31" }
      ] }
    ]
  });
  const files = [
    file(1, "剧名.S01E01.1080p.mkv"),
    file(2, "剧名.S02E30.1080p.mkv"),
    file(3, "剧名 第31集.mkv"),
    file(4, "剧名.2020.01.30.mkv"),
    file(5, "剧名.完全对不上.mkv")
  ];
  const result = computeGroupStrategyAssignments({ kind: "episodeGroup", episodeGroupId: "gid1" }, files, null, { fallbackSeason: 1, episodeGroupDetail: detail });
  // 原始 S01E01 → 子组1第1集
  assert.deepEqual([seasonOf(result.assignments, 1).seasonNumber, seasonOf(result.assignments, 1).episodeNumber], [1, 1]);
  // 原始 S02E30 → 子组2第1集
  assert.deepEqual([seasonOf(result.assignments, 2).seasonNumber, seasonOf(result.assignments, 2).episodeNumber], [2, 1]);
  // 绝对集号 31 在组内唯一 → 子组2第2集（保留集名与播出日期）
  assert.deepEqual([seasonOf(result.assignments, 3).seasonNumber, seasonOf(result.assignments, 3).episodeNumber], [2, 2]);
  assert.equal(seasonOf(result.assignments, 3).name, "第三十一集");
  // 无季集但有播出日期 → 唯一命中
  assert.deepEqual([seasonOf(result.assignments, 4).seasonNumber, seasonOf(result.assignments, 4).episodeNumber], [2, 1]);
  // 完全无法匹配 → 保留原识别并计入警告
  assert.equal(result.assignments.get("5"), undefined);
  assert.ok(result.warnings.some((message) => message.includes("未能匹配")));
});

test("剧集组策略：文件季号不在剧集组原始季里时不拿集号当顺序号兜底（狐妖式防错位）", () => {
  // 单季剧的剧集组：origin 全是 1xEy
  const detail = normalizeEpisodeGroupDetail({
    id: "gid2",
    groups: [
      { id: "g1", name: "全一季", order: 1, episodes: [
        { id: "e5", order: 5, season_number: 1, episode_number: 5, name: "第五集", air_date: "2020-01-05" },
        { id: "e172", order: 172, season_number: 1, episode_number: 172, name: "第一百七十二集", air_date: "2023-08-05" }
      ] }
    ]
  });
  const files = [
    file(1, "狐妖小红娘S13E05.mkv"),
    file(2, "狐妖小红娘 第172集.mkv"),
    file(3, "狐妖小红娘.S01E05.mkv")
  ];
  const result = computeGroupStrategyAssignments({ kind: "episodeGroup", episodeGroupId: "gid2" }, files, null, { fallbackSeason: 1, episodeGroupDetail: detail });
  // S13 不在剧集组原始季里 → 不把 5 当顺序号错配到第 5 集，保留原识别并计入未匹配
  assert.equal(result.assignments.get("1"), undefined);
  // 无季号的绝对集号照样命中（第172集 → origin 1x172 → 组内第 2 位）
  assert.equal(seasonOf(result.assignments, 2).episodeNumber, 2);
  // S01 在组里 → 精确命中（origin 1x5 → 组内第 1 位）
  assert.equal(seasonOf(result.assignments, 3).episodeNumber, 1);
  assert.ok(result.warnings.some((message) => message.includes("未能匹配")));
});

test("refreshOrganizeGroupTargets：拆分策略写入目标季集与 Season 目录（锁定优先于策略，策略优先于手动计划）", () => {
  const config = structuredClone(DEFAULT_CONFIG);
  const media = { id: 42, mediaType: "tv", seasons: [{ season_number: 1, episode_count: 2 }, { season_number: 2, episode_count: 2 }] };
  const group = {
    id: "g1",
    title: "测试剧",
    targetSeason: "",
    sourceFolders: [],
    fields: { mediaType: "tv", title: "测试剧", year: "2020", season: "1" },
    media,
    files: [
      file(1, "测试剧 S01E01.mkv", { fields: { mediaType: "tv" } }),
      file(2, "测试剧 S01E02.mkv", { fields: { mediaType: "tv" } }),
      file(3, "测试剧 S01E03.mkv", { fields: { mediaType: "tv" } }),
      file(4, "测试剧 S01E04.mkv", { fields: { mediaType: "tv" } })
    ]
  };
  refreshOrganizeGroupTargets(group, config, {
    inPlace: true,
    strategies: { g1: { kind: "splitSequential" } },
    episodePlans: { g1: { targetSeason: "9" } }
  });
  assert.equal(group.files[0].fields.seasonEpisode, "S01E01");
  assert.equal(group.files[1].fields.seasonEpisode, "S01E02");
  assert.equal(group.files[2].fields.seasonEpisode, "S02E01");
  assert.equal(group.files[3].fields.seasonEpisode, "S02E02");
  assert.equal(group.files[0].fields.seasonFolder, "Season 1");
  assert.equal(group.files[2].fields.seasonFolder, "Season 2");
  assert.deepEqual(plain(group.files[2].folderParts), ["测试剧 (2020)", "Season 2"]);
  assert.equal(group.files[0].matched, true);
  assert.match(group.files[0].fields.tmdbMatchReason, /策略/);

  // 手动锁定覆盖策略
  const lockedGroup = structuredClone(group);
  lockedGroup.files = group.files.map((item) => structuredClone(item));
  refreshOrganizeGroupTargets(lockedGroup, config, {
    inPlace: true,
    strategies: { g1: { kind: "splitSequential" } },
    episodeLocks: { "2": { seasonNumber: 0, episodeNumber: 5, seasonEpisode: "S00E05", name: "特别篇", airDate: "" } }
  });
  assert.equal(lockedGroup.files[1].fields.seasonEpisode, "S00E05");
  assert.equal(lockedGroup.files[2].fields.seasonEpisode, "S02E01");
});

test("refreshOrganizeGroupTargets：合并策略虚拟分组（被并入分组无策略记录也能应用编号）", () => {
  const config = structuredClone(DEFAULT_CONFIG);
  const makeGroup = (id, season, ids) => ({
    id,
    title: "柯南",
    targetSeason: "",
    sourceFolders: [],
    fields: { mediaType: "tv", title: "柯南", season: String(season) },
    media: null,
    files: ids.map((id2) => file(id2, `柯南 S0${season}E01.mkv`, { fields: { mediaType: "tv" } }))
  });
  const groupA = makeGroup("A", 1, ["a1"]);
  const groupB = makeGroup("B", 2, ["b1", "b2"]);
  const strategyAssignments = prepareMergeStrategyAssignments([groupA, groupB], { A: { kind: "merge" } }, { A: [{ groupId: "A", fileIds: ["a1"] }, { groupId: "B", fileIds: ["b1", "b2"] }] });
  refreshOrganizeGroupTargets(groupB, config, { inPlace: true, strategyAssignments });
  assert.equal(groupB.files[0].fields.seasonEpisode, "S01E02");
  assert.equal(groupB.files[1].fields.seasonEpisode, "S01E03");
  assert.equal(groupB.files[0].fields.seasonFolder, "Season 1");
});

test("refreshOrganizeGroupTargets：电影按合集归档在目标路径插入合集层级，可按分组覆盖", () => {
  const config = structuredClone(DEFAULT_CONFIG);
  assert.equal(config.library.collectionFolder, false);
  const makeMovieGroup = () => ({
    id: "m1",
    title: "星球大战4",
    targetSeason: "",
    sourceFolders: [],
    fields: { mediaType: "movie", title: "星球大战4", year: "1977", category: "电影" },
    media: { id: 11, mediaType: "movie", collectionId: 10, collectionName: "星球大战（系列）" },
    files: [file("f1", "星战4.mkv", { fields: { mediaType: "movie" } })]
  });

  const offGroup = makeMovieGroup();
  refreshOrganizeGroupTargets(offGroup, config, { inPlace: false, collectionByGroup: {} });
  assert.deepEqual(plain(offGroup.files[0].folderParts), ["电影", "星球大战4 (1977)"]);

  const onGroup = makeMovieGroup();
  refreshOrganizeGroupTargets(onGroup, config, { inPlace: false, collectionByGroup: { m1: true } });
  assert.deepEqual(plain(onGroup.files[0].folderParts), ["电影", "星球大战（系列）", "星球大战4 (1977)"]);

  // 原地模式同样插入合集层级
  const inPlaceGroup = makeMovieGroup();
  refreshOrganizeGroupTargets(inPlaceGroup, config, { inPlace: true, collectionByGroup: { m1: true } });
  assert.deepEqual(plain(inPlaceGroup.files[0].folderParts), ["星球大战（系列）", "星球大战4 (1977)"]);
});

test("refreshOrganizeGroupTargets：旁挂字幕跟随策略后的主文件季集", () => {
  const config = structuredClone(DEFAULT_CONFIG);
  const media = { id: 42, mediaType: "tv", seasons: [{ season_number: 1, episode_count: 1 }, { season_number: 2, episode_count: 2 }] };
  const group = {
    id: "g1",
    title: "测试剧",
    targetSeason: "",
    sourceFolders: [],
    fields: { mediaType: "tv", title: "测试剧", season: "1" },
    media,
    files: [
      file(1, "测试剧 S01E01.mkv", { fields: { mediaType: "tv" } }),
      file(2, "测试剧 S01E02.mkv", { fields: { mediaType: "tv" } }),
      file(3, "测试剧 S01E02.ass", { fields: { mediaType: "tv" } })
    ]
  };
  refreshOrganizeGroupTargets(group, config, { inPlace: true, strategies: { g1: { kind: "splitSequential" } } });
  assert.equal(group.files[0].fields.seasonEpisode, "S01E01");
  assert.equal(group.files[1].fields.seasonEpisode, "S02E01");
  // 字幕沿用主文件 S01E02 → 拆分后的 S02E01
  assert.equal(group.files[2].fields.seasonEpisode, "S02E01");
});

test("normalizeTmdbMedia 与模板回归：默认配置模板可渲染策略后的季集", () => {
  assert.equal(isVideoFile("测试 S01E01.mkv"), true);
  assert.deepEqual(plain(parseSeasonEpisode("测试剧 S01E02.mkv", 1)), { season: 1, episode: 2, endEpisode: 0, tokenStart: 4, tokenLength: 6, seasonEpisode: "S01E02" });
});

// —— 真实数据驱动拆分（对号入座）——

const bangumiStructure = (...counts) => structureFromBangumiChain(counts.map((eps, index) => ({ id: index + 1, name: `期${index + 1}`, name_cn: `第${index + 1}期`, date: "", eps, total_episodes: eps })));

test("顺序拆多季·对号入座：缺集不再让后续文件错位", () => {
  const structure = bangumiStructure(24, 24, 24);
  const files = [];
  for (let episode = 1; episode <= 60; episode += 1) {
    if (episode === 35) continue;
    files.push(file(episode, `咒术回战 E${String(episode).padStart(3, "0")}.mkv`));
  }
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 24).seasonEpisode, "S01E24");
  assert.equal(seasonOf(result.assignments, 25).seasonEpisode, "S02E01");
  // 缺了 E35，E36 仍然对号入座到 S02E12（旧顺序占位会错成 S02E11）
  assert.equal(seasonOf(result.assignments, 34).seasonEpisode, "S02E10");
  assert.equal(seasonOf(result.assignments, 36).seasonEpisode, "S02E12");
  assert.equal(seasonOf(result.assignments, 48).seasonEpisode, "S02E24");
  assert.equal(seasonOf(result.assignments, 49).seasonEpisode, "S03E01");
  assert.equal(seasonOf(result.assignments, 60).seasonEpisode, "S03E12");
  assert.deepEqual(plain(result.warnings), []);
});

test("顺序拆多季·对号入座：绝对集号反查目标季集（咒术回战 E47）", () => {
  const structure = bangumiStructure(24, 23);
  const files = [file(1, "咒术回战 S01E047.mkv"), file(2, "咒术回战 E025.mkv"), file(3, "咒术回战 E001.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 3).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S02E01");
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S02E23");
  assert.deepEqual(plain(result.warnings), []);
});

test("顺序拆多季·对号入座：季集本来就合法的文件原样保留", () => {
  const structure = bangumiStructure(24, 23);
  const files = [file(1, "咒术回战 S01E012.mkv"), file(2, "咒术回战 S02E11.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E12");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S02E11");
  assert.deepEqual(plain(result.warnings), []);
});

test("顺序拆多季·对号入座：无集号文件排在已入座位置之后，重复集号顺延并警示", () => {
  const structure = bangumiStructure(24, 24);
  const files = [file(1, "咒术回战 E002.mkv"), file(2, "咒术回战 NCOP.mkv"), file(3, "咒术回战 E010.mkv"), file(4, "咒术回战 E010 v2.mkv"), file(5, "咒术回战 E012.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E02");
  // 重复的 E010 两条文件一条占 E10、一条顺延 E11（同集号先到先得）
  assert.deepEqual([seasonOf(result.assignments, 3).seasonEpisode, seasonOf(result.assignments, 4).seasonEpisode].sort(), ["S01E10", "S01E11"]);
  assert.equal(seasonOf(result.assignments, 5).seasonEpisode, "S01E12");
  // 无集号文件排到已入座最大位置之后
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E13");
  assert.ok(result.warnings.some((message) => message.includes("集号存在重复")));
});

test("顺序拆多季·单季结构不拆（死神 366 集合并型）", () => {
  const tvJson = parseCureTmdbTvJson({ 30984: { name: "死神", seasons: [{ season_number: 0, name: "特别篇", episode_count: 4 }, { season_number: 1, name: "本篇", episode_count: 366 }] } });
  const structure = structureFromCureTmdbEntry(30984, tvJson["30984"]);
  assert.equal(structureIsSingleSeason(structure), true);
  assert.deepEqual(plain(structure.seasons), [{ season: 1, count: 366, name: "本篇", date: "" }]);
  const files = [file(1, "死神 E001.mkv"), file(2, "死神 E002.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(result.assignments.size, 0);
  assert.ok(result.warnings[0].includes("只有一季"));
  assert.ok(result.warnings[0].includes("合并策略"));
});

test("顺序拆多季·连载中集数未知：只覆盖已知集数并提示", () => {
  const structure = bangumiStructure(24, 0);
  assert.equal(structure.incomplete, true);
  const files = [file(1, "新番 E001.mkv"), file(2, "新番 E024.mkv"), file(3, "新番 E025.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitSequential" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S01E01");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S01E24");
  // 超出已知总集数 → 顺延到最后一季
  assert.equal(seasonOf(result.assignments, 3).seasonEpisode, "S01E25");
  assert.ok(result.warnings.some((message) => message.includes("集数未知")));
  assert.ok(result.warnings.some((message) => message.includes("超出总集数")));
});

test("跳序拆多季·Bangumi 来源提示建议改用顺序拆", () => {
  const structure = { ...bangumiStructure(61, 16, 42), source: "bangumi", sourceLabel: "Bangumi 期结构" };
  const files = [file(1, "海贼王 E062.mkv"), file(2, "海贼王 E100.mkv")];
  const result = computeGroupStrategyAssignments({ kind: "splitKeep" }, files, null, { fallbackSeason: 1, structure });
  assert.equal(seasonOf(result.assignments, 1).seasonEpisode, "S02E62");
  assert.equal(seasonOf(result.assignments, 2).seasonEpisode, "S03E100");
  assert.ok(result.warnings.some((message) => message.includes("建议改用")));
});

test("策略标签带数据来源后缀，kindIsSplitStrategy 只认两种拆分", () => {
  assert.equal(organizeStrategyLabel({ kind: "splitSequential" }), "单季顺序拆多季");
  assert.equal(organizeStrategyLabel({ kind: "splitSequential", structureSource: "bangumi" }), "单季顺序拆多季（Bangumi）");
  assert.equal(organizeStrategyLabel({ kind: "splitKeep", structureSource: "curetmdb" }), "单季跳序拆多季（CureTMDb）");
  assert.equal(organizeStrategyLabel({ kind: "merge", structureSource: "bangumi" }), "多季合并一季（Bangumi）");
  assert.equal(organizeStrategyLabel({ kind: "merge" }), "多季合并一季");
  assert.equal(kindIsSplitStrategy("splitSequential"), true);
  assert.equal(kindIsSplitStrategy("splitKeep"), true);
  assert.equal(kindIsSplitStrategy("merge"), false);
  assert.equal(kindIsSplitStrategy("default"), false);
});

test("parseCureTmdbTvJson：按 TMDB id 解析社区分季表并过滤脏数据", () => {
  const map = parseCureTmdbTvJson({
    "223564": { name: "超超超超超喜欢你的100个女朋友", seasons: [{ season_number: 1, name: "第一季", episode_count: 12 }, { season_number: 2, name: "第二季", episode_count: 12 }] },
    "95479": { name: "咒术回战", seasons: [{ season_number: 1, name: "咒术回战", episode_count: 24 }, { season_number: 2, name: "涩谷事变", episode_count: 23 }, { season_number: 3, name: "死灭回游", episode_count: 24 }] },
    "not-a-number": { name: "坏条目", seasons: [{ season_number: 1, episode_count: 12 }] },
    "111": { name: "空季表", seasons: [] },
    "222": { name: "坏集数", seasons: [{ season_number: 1, episode_count: -3 }] }
  });
  assert.deepEqual(Object.keys(map).sort(), ["223564", "95479"]);
  const structure = structureFromCureTmdbEntry(95479, map["95479"]);
  assert.equal(structure.source, "curetmdb");
  assert.equal(structure.name, "咒术回战");
  assert.deepEqual(plain(structure.seasons.map((season) => [season.season, season.count])), [[1, 24], [2, 23], [3, 24]]);
  // 空季表与坏集数条目整个丢弃
  assert.equal(map["111"], undefined);
  assert.equal(map["222"], undefined);
});

test("matchBangumiSubject：译名/原名命中 + 年份 ±1，剧场版与年份差过大拒绝", () => {
  const candidates = [
    { id: 294993, type: 2, platform: "TV", name: "呪術廻戦", name_cn: "咒术回战", date: "2020-10-02" },
    { id: 369304, type: 2, platform: "TV", name: "呪術廻戦 懐玉・玉折/渋谷事変", name_cn: "咒术回战 第二季", date: "2023-07-06" },
    { id: 100, type: 2, platform: "剧场版", name: "咒术回战 0", name_cn: "剧场版 咒术回战 0", date: "2021-12-24" },
    { id: 200, type: 2, platform: "TV", name: "咒术回战 外传", name_cn: "咒术回战 外传", date: "2013-10-02" }
  ];
  const media = { id: 95479, mediaType: "tv", title: "咒术回战", originalTitle: "呪術廻戦", year: "2020" };
  const matched = matchBangumiSubject(candidates, media);
  assert.equal(matched?.id, 294993);
  // 名字相等但年份差 >1 被拒
  assert.equal(matchBangumiSubject([{ id: 300, type: 2, platform: "TV", name: "咒术回战 旧版", name_cn: "咒术回战", date: "2013-10-02" }], media), null);
  // 平台非 TV 被拒
  assert.equal(matchBangumiSubject([{ id: 301, type: 2, platform: "剧场版", name: "咒术回战 0", name_cn: "咒术回战", date: "2021-12-24" }], media), null);
  // 译名完全相等优先
  const frieren = matchBangumiSubject([
    { id: 1, type: 2, platform: "TV", name: "葬送のフリーレン", name_cn: "芙莉莲", date: "2023-09-29" },
    { id: 2, type: 2, platform: "TV", name: "葬送的芙莉莲", name_cn: "葬送的芙莉莲", date: "2023-09-29" }
  ], { id: 9, mediaType: "tv", title: "葬送的芙莉莲", year: "2023" });
  assert.equal(frieren?.id, 2);
  // 网盘番/国产动画 platform=WEB 也命中；包含匹配兼容「凡人修仙传」vs「凡人修仙传之凡人风起天南」
  const donghua = matchBangumiSubject([
    { id: 1, type: 2, platform: "WEB", name: "凡人修仙传之凡人风起天南", name_cn: "凡人修仙传之凡人风起天南", date: "2020-07-25" },
    { id: 2, type: 2, platform: "WEB", name: "凡人修仙传之凡人风起天南 贰", name_cn: "凡人修仙传之凡人风起天南 贰", date: "2020-10-11" }
  ], { id: 9, mediaType: "tv", title: "凡人修仙传", year: "2020" });
  assert.equal(donghua?.id, 1);
  // 同年多候选取标题更短的（裸标题通常是第一期）
  const spy = matchBangumiSubject([
    { id: 10, type: 2, platform: "TV", name: "SPY×FAMILY", name_cn: "间谍过家家", date: "2022-04-09" },
    { id: 11, type: 2, platform: "TV", name: "SPY×FAMILY 第2クール", name_cn: "间谍过家家 第2部分", date: "2022-10-01" }
  ], { id: 8, mediaType: "tv", title: "间谍过家家", year: "2022" });
  assert.equal(spy?.id, 10);
});

test("Bangumi 续集链：只跟「续集」、词干相容、日期不回退，串出期结构", async () => {
  const relations = {
    100: [
      { id: 369304, type: 2, relation: "续集", name: "呪術廻戦 懐玉・玉折", name_cn: "咒术回战 怀玉·玉折 / 涩谷事变", date: "2023-07-06" },
      { id: 331559, type: 2, relation: "前传", name: "劇場版 呪術廻戦 0", name_cn: "剧场版 咒术回战 0", date: "2021-12-24" },
      { id: 373584, type: 4, relation: "游戏", name: "咒术回战 幻影夜行", name_cn: "咒术回战 幻影夜行", date: "2023-11-21" }
    ],
    369304: [
      { id: 400, type: 2, relation: "续集", name: "呪術廻戦 死滅回游", name_cn: "咒术回战 死灭回游", date: "2026-01-08" }
    ]
  };
  const details = {
    100: { id: 100, type: 2, platform: "TV", name: "呪術廻戦", name_cn: "咒术回战", date: "2020-10-02", eps: 24, total_episodes: 25 },
    369304: { id: 369304, type: 2, platform: "TV", name: "呪術廻戦 懐玉・玉折", name_cn: "咒术回战 怀玉·玉折 / 涩谷事变", date: "2023-07-06", eps: 23, total_episodes: 24 },
    400: { id: 400, type: 2, platform: "TV", name: "呪術廻戦 死滅回游", name_cn: "咒术回战 死灭回游", date: "2026-01-08", eps: 0, total_episodes: 0 }
  };
  const candidates = bangumiSequelCandidates(details[100], relations[100]);
  assert.deepEqual(plain(candidates), [{ id: 369304, date: "2023-07-06" }]);
  // 词干不相容：龙珠Z 的续集不是龙珠GT 的语义同源
  assert.deepEqual(plain(bangumiSequelCandidates({ name_cn: "龙珠Z", date: "1989-04-26" }, [{ id: 9, type: 2, relation: "续集", name_cn: "龙珠GT", date: "1996-02-07" }])), []);
  // 「系列名+期号」式命名（狐妖小红娘1 下沙篇 → 狐妖小红娘2 王权篇）互不包含，但同一系列词干+新期号要认
  const huyao = bangumiSequelCandidates(
    { name_cn: "狐妖小红娘1 下沙篇", date: "2015-06-26" },
    [
      { id: 11, type: 2, relation: "续集", name_cn: "狐妖小红娘2 王权篇", date: "2016-01-08" },
      { id: 12, type: 2, relation: "续集", name_cn: "狐妖小红娘GT", date: "2016-02-01" }
    ]
  );
  assert.deepEqual(plain(huyao), [{ id: 11, date: "2016-01-08" }]);
  // 日期回退拒绝
  assert.deepEqual(plain(bangumiSequelCandidates({ name_cn: "某番", date: "2023-07-06" }, [{ id: 9, type: 2, relation: "续集", name_cn: "某番 前篇", date: "2020-01-01" }])), []);
  const chain = await buildBangumiChainSubjects(details[100], async (id) => details[id] || null, async (id) => relations[id] || []);
  assert.deepEqual(plain(chain.map((subject) => subject.id)), [100, 369304, 400]);
  const structure = structureFromBangumiChain(chain);
  assert.equal(structure.source, "bangumi");
  assert.equal(structure.name, "咒术回战");
  assert.deepEqual(plain(structure.seasons.map((season) => [season.season, season.count])), [[1, 24], [2, 23], [3, 0]]);
  assert.equal(structure.incomplete, true);
  // 非 TV 锚点拒绝串链
  assert.deepEqual(plain(await buildBangumiChainSubjects({ ...details[100], platform: "剧场版" }, async () => null, async () => [])), []);
});

test("结构工具：chips 文本、cache key 与冷缓存回退 TMDB", () => {
  assert.equal(structureSeasonChipsText({ seasons: [{ season: 1, count: 24 }, { season: 2, count: 23 }, { season: 3, count: 0 }] }), "S1 24集 \xB7 S2 23集");
  assert.equal(structureSeasonChipsText({ seasons: [] }), "无正片季数据");
  const media = { id: 95479, mediaType: "tv", title: "咒术回战", seasons: [{ season_number: 1, episode_count: 24 }] };
  assert.equal(strategyStructureCacheKey(media, "bangumi"), "tv:95479:bangumi");
  const bangumi = bangumiStructure(24, 23);
  assert.equal(resolveStrategyStructure({ kind: "splitSequential", structureSource: "bangumi" }, media, { "tv:95479:bangumi": bangumi }).source, "bangumi");
  // 冷缓存回退 TMDB 官方结构
  const fallback = resolveStrategyStructure({ kind: "splitSequential", structureSource: "bangumi" }, media, {});
  assert.equal(fallback.source, "tmdb");
  assert.deepEqual(plain(fallback.seasons), [{ season: 1, count: 24, name: "", date: "" }]);
  // 无 structureSource 的旧策略记录 → TMDB 官方
  assert.equal(resolveStrategyStructure({ kind: "splitSequential" }, media, null).source, "tmdb");
  assert.deepEqual(plain(splitSeasonBounds(tmdbStructureFromMedia(media))), [{ season: 1, from: 1, to: 24, count: 24 }]);
});

await chain;
console.log(`\n整理策略测试通过：${passed} 项`);
