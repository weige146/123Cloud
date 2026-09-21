// 《初入职场·中医季》(TMDB 122562) 真实场景回归测试：
// 1. 自动识别「主标题·命名季」（初入职场·中医季，文件年 2025 vs 首播年 2021）；
// 2. TMDB 校准：命名季 S00 特别篇（药食同源/加更版）进入候选池并按期数精确匹配；
// 3. 变体命名：Part01/药食同源/加更版插在季集记号之后、技术参数之前。
// 用法：node 油猴脚本/tests/workplace-tcm-season.test.mjs
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
globalThis.__m = { chooseTmdbCandidate, tmdbCandidateHasExactTitle, previewEpisodeCalibration, injectNameVariant };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { chooseTmdbCandidate, tmdbCandidateHasExactTitle, previewEpisodeCalibration, injectNameVariant } = sandbox.__m;

// —— TMDB 122562 真实季集数据（2026-08 从 themoviedb.org 核对，zh-CN 简体形态）——
const ep = (season, episode, name, airDate) => ({ id: `s${season}e${episode}`, seasonNumber: season, episodeNumber: episode, name, airDate });
const SEASON_5 = [
  ["第 1 期上：中医面试赛制升级", "2025-09-14"], ["第 1 期下：“脆皮人”中医指南", "2025-09-14"],
  ["第 2 期上：凭脉赶走坏情绪", "2025-09-21"], ["第 2 期下：情绪患者凭脉辨证", "2025-09-21"],
  ["第 3 期上：院人问诊体重管理", "2025-09-28"], ["第 3 期下：中医实现体重自由", "2025-09-28"],
  ["第 4 期上：中医助眠好好睡觉", "2025-10-05"], ["第 4 期下：院人花式入睡大赏", "2025-10-05"],
  ["第 5 期上：哥哥们体验中医体检", "2025-10-12"], ["第 5 期下：中医教你防治未老先衰", "2025-10-12"]
].map(([name, airDate], index) => ep(5, index + 1, name, airDate));
const SPECIAL_DATES = { 1: ["2025-09-14", "2025-09-15"], 2: ["2025-09-21", "2025-09-22"], 3: ["2025-09-28", "2025-09-29"], 4: ["2025-10-05", "2025-10-06"], 5: ["2025-10-12", "2025-10-13"] };
const SPECIALS = [];
for (let n = 1; n <= 5; n += 1) {
  SPECIALS.push(ep(0, 98 + n * 2, `中医季 药食同源第 ${n} 期：特别篇标题${n}`, SPECIAL_DATES[n][0]));
  SPECIALS.push(ep(0, 99 + n * 2, `中医季 加更版第 ${n} 期：加更标题${n}`, SPECIAL_DATES[n][1]));
}
// 其它季的命名季特别篇，必须被排除在目标季候选池之外
SPECIALS.push(ep(0, 116, "金融季 加更版第 1 期：专家开扒", "2026-03-25"));
SPECIALS.push(ep(0, 28, "职场特辑：北大职场交流分享会", "2021-10-01"));
const TMDB_MEDIA = {
  id: 122562, mediaType: "tv", name: "初入职场", year: "2021",
  seasons: [
    { season_number: 0, name: "特别篇", air_date: "2021-08-14" },
    ...[1, 2, 3, 4, 6].map((n) => ({ season_number: n, name: `第 ${n} 季`, air_date: `${2020 + n}-01-01` })),
    { season_number: 5, name: "中医季", air_date: "2025-09-14" }
  ]
};
const stubTmdb = {
  async details() { return TMDB_MEDIA; },
  async season(_id, season) { return season === 5 ? SEASON_5 : season === 0 ? SPECIALS : []; }
};

// —— 网盘真实文件名（UBWEB 发布组：正片上下分部 + 药食同源 + 加更版）——
const pad = (n) => String(n).padStart(2, "0");
const names = [];
for (let n = 1; n <= 5; n += 1) {
  const date = SEASON_5[(n - 1) * 2].airDate.replace(/-/g, "");
  const token = `S05E${pad(n)}`;
  names.push(`[${date}][初入职场·中医季 第${pad(n)}期 上].Workplace.Newcomers.2025.${token}.Part01.2160p.WEB-DL.H264.AAC-UBWEB.mp4`);
  names.push(`[${date}][初入职场·中医季 第${pad(n)}期 下].Workplace.Newcomers.2025.${token}.Part02.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
  names.push(`[${date}][初入职场·中医季 药食同源 第${pad(n)}期].Workplace.Newcomers.Food.Medicine.Homolog.2025.${token}.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
  const extraDate = SPECIAL_DATES[n][1].replace(/-/g, "");
  names.push(`[${extraDate}][初入职场·中医季 加更版 第${pad(n)}期].Workplace.Newcomers.Extra.Version.2025.${token}.2160p.WEB-DL.H265.AAC-UBWEB.mp4`);
}
const group = (files, extraFields = {}) => ({ fields: { mediaType: "tv", season: "5", ...extraFields }, media: TMDB_MEDIA, files: files.map((name, index) => ({ id: String(index + 1), name })) });

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    await fn();
    passed += 1;
    console.log(`  ok ${passed} - ${title}`);
  });
};

// —— 问题 1：自动识别（初入职场·中医季 → TMDB 初入职场）——
const CANDIDATES = [
  { id: 122562, media_type: "tv", name: "初入职场", original_name: "初入職場", first_air_date: "2021-09-08", popularity: 5 },
  { id: 999, media_type: "tv", name: "别的节目", original_name: "别的节目", first_air_date: "2025-01-01", popularity: 30 }
];
test("自动识别：主标题·命名季 + 季播出年命中主标题剧集", () => {
  const fields = { title: "初入职场·中医季", year: "2025", mediaType: "tv", season: "5" };
  const chosen = chooseTmdbCandidate(CANDIDATES, fields);
  assert.ok(chosen, "应命中初入职场而不是返回 null");
  assert.equal(chosen.id, 122562);
  assert.equal(tmdbCandidateHasExactTitle(CANDIDATES[0], "初入职场·中医季"), true);
});
test("自动识别：纯主标题不受影响（回归保护）", () => {
  const fields = { title: "初入职场", year: "2021", mediaType: "tv", season: "1" };
  assert.equal(chooseTmdbCandidate(CANDIDATES, fields).id, 122562);
});
test("自动识别：命名季别名对不上时不误配主标题剧集（守卫）", () => {
  const fields = { title: "完全无关·中医季", year: "2025", mediaType: "tv", season: "5" };
  const chosen = chooseTmdbCandidate(CANDIDATES, fields);
  assert.ok(!chosen || chosen.id !== 122562, "不应误配到初入职场");
});

// —— 问题 2：TMDB 校准（药食同源/加更版命中 S00 特别篇）——
test("端到端：正片上下 + 药食同源 + 加更版 全部校准且期数一一对应", async () => {
  const result = await previewEpisodeCalibration(stubTmdb, group(names));
  const matches = result.matches;
  const of = (index) => matches[String(index + 1)];
  const issueOf = (name) => Number(name.match(/第(\d{2})期/)[1]);
  for (let index = 0; index < names.length; index += 1) {
    assert.ok(of(index), `文件未校准：${names[index]}`);
    const issue = issueOf(names[index]);
    const match = of(index);
    if (/第\d{2}期 上\]/.test(names[index])) {
      assert.equal(`${match.seasonNumber}:${match.episodeNumber}`, `5:${issue * 2 - 1}`, `正片上错误：${names[index]}`);
    } else if (/第\d{2}期 下\]/.test(names[index])) {
      assert.equal(`${match.seasonNumber}:${match.episodeNumber}`, `5:${issue * 2}`, `正片下错误：${names[index]}`);
    } else if (names[index].includes("药食同源")) {
      assert.equal(`${match.seasonNumber}:${match.episodeNumber}`, `0:${98 + issue * 2}`, `药食同源期数错误：${names[index]}`);
    } else if (names[index].includes("加更版")) {
      assert.equal(`${match.seasonNumber}:${match.episodeNumber}`, `0:${99 + issue * 2}`, `加更版期数错误：${names[index]}`);
    }
  }
});
test("命名季别名守卫：其它季（金融季）的特别篇不进候选池", async () => {
  const result = await previewEpisodeCalibration(stubTmdb, group([names[0]]));
  assert.ok(!result.episodes.some((episode) => episode.name.includes("金融季")), "金融季特别篇不应出现在候选池");
});

// —— 问题 3：变体标签插在季集记号之后、技术参数之前 ——
test("变体标签命名位置与发布组习惯一致", () => {
  const base = "Workplace Newcomers.2021.S05E01.2160p.WEB-DL.H264.AAC-UBWEB.mp4";
  assert.equal(injectNameVariant(base, "Part01"), "Workplace Newcomers.2021.S05E01.Part01.2160p.WEB-DL.H264.AAC-UBWEB.mp4");
  assert.equal(injectNameVariant(base, "药食同源"), "Workplace Newcomers.2021.S05E01.药食同源.2160p.WEB-DL.H264.AAC-UBWEB.mp4");
  assert.equal(injectNameVariant("初入职场.2025.2160p.WEB-DL.mp4", "加更版"), "初入职场.2025.2160p.WEB-DL.加更版.mp4");
  const tagged = injectNameVariant(base, "加更版");
  assert.equal(injectNameVariant(tagged, "加更版"), tagged, "幂等");
});

await chain;
console.log(`\n全部 ${passed} 个用例通过`);
