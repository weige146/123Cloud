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
  refreshOrganizeGroupTargets
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
  refreshOrganizeGroupTargets
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
  assert.match(result.warnings[0], /超出 TMDB 总集数/);
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
  assert.ok(result.warnings.some((message) => message.includes("超出 TMDB 总集数")));
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
      file(2, "测试剧 S02E02.mkv", { fields: { mediaType: "tv" } }),
      file(3, "测试剧 S02E02.ass", { fields: { mediaType: "tv" } })
    ]
  };
  refreshOrganizeGroupTargets(group, config, { inPlace: true, strategies: { g1: { kind: "splitSequential" } } });
  assert.equal(group.files[0].fields.seasonEpisode, "S01E01");
  assert.equal(group.files[1].fields.seasonEpisode, "S02E01");
  // 字幕沿用主文件 S02E02 → 拆分后的 S02E01
  assert.equal(group.files[2].fields.seasonEpisode, "S02E01");
});

test("normalizeTmdbMedia 与模板回归：默认配置模板可渲染策略后的季集", () => {
  assert.equal(isVideoFile("测试 S01E01.mkv"), true);
  assert.deepEqual(plain(parseSeasonEpisode("测试剧 S01E02.mkv", 1)), { season: 1, episode: 2, endEpisode: 0, seasonEpisode: "S01E02" });
});

await chain;
console.log(`\n整理策略测试通过：${passed} 项`);
