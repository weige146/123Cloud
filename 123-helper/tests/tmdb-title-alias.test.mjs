// TMDB 标题别名回归测试：覆盖「英文命名 → TMDB 英文别名；中文/兼容 → 中文别名；
// 无英文别名 → 回退中文别名」的预期逻辑，并回归两个历史 BUG：
// 1) enrichMediaTitleAliases 曾用文件名英文兜底 englishTitles（RC-1）；
// 2) normalizeTmdbMedia 曾把旧 media.englishTitles 放回候选首位，导致污染自固化（RC-2）。
// 用法：node 油猴脚本/tests/tmdb-title-alias.test.mjs
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
globalThis.__tmdbAlias = {
  normalizeTmdbMedia, enrichMediaTitleAliases, titleAliasOptions, applyTmdbFields,
  resolveNamingMode, tmdbCandidateTitles, scoreTmdbCandidate
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { normalizeTmdbMedia, enrichMediaTitleAliases, titleAliasOptions, applyTmdbFields, resolveNamingMode, scoreTmdbCandidate } = sandbox.__tmdbAlias;

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    await fn();
    passed += 1;
    console.log(`  ok ${title}`);
  });
};

// —— 测试夹具 ——
// 真实数据（TMDB 1315772 小黄人与大怪兽）：US 地区有 3 个英文别名，
// 其他地区是西/德/波兰语等纯 ASCII 本地化标题，不应混入英文别名。
const minions3 = {
  media_type: "movie",
  id: 1315772,
  title: "小黄人大眼萌3",
  original_title: "Minions & Monsters",
  original_language: "en",
  release_date: "2026-07-01",
  alternative_titles: { titles: [
    { iso_3166_1: "US", title: "Illumination's Minions & Monsters" },
    { iso_3166_1: "US", title: "Minions 3" },
    { iso_3166_1: "US", title: "Minions and Monsters" },
    { iso_3166_1: "BR", title: "Minions e Monstros" },
    { iso_3166_1: "DE", title: "Minions und Monster" },
    { iso_3166_1: "PL", title: "Minionki 3" }
  ] },
  translations: { translations: [
    { iso_639_1: "zh", iso_3166_1: "CN", data: { title: "小黄人大眼萌3" } }
  ] }
};
// 有英文别名的影片：zh-CN 翻译 + en 翻译 + US 区域别名
const withEnglish = {
  media_type: "movie",
  id: 843,
  title: "花样年华",
  original_title: "花樣年華",
  original_language: "zh",
  release_date: "2000-09-29",
  alternative_titles: { titles: [{ iso_3166_1: "US", title: "In the Mood for Love" }] },
  translations: { translations: [
    { iso_639_1: "zh", iso_3166_1: "CN", data: { title: "花样年华" } },
    { iso_639_1: "en", iso_3166_1: "US", data: { title: "In the Mood for Love" } }
  ] }
};
// 无英文别名的影片：只有中文翻译/别名
const noEnglish = {
  media_type: "tv",
  id: 9001,
  name: "初入职场",
  original_name: "初入職場",
  original_language: "zh",
  first_air_date: "2025-09-14",
  alternative_titles: { titles: [{ iso_3166_1: "CN", title: "初入职场" }] },
  translations: { translations: [
    { iso_639_1: "zh", iso_3166_1: "CN", data: { name: "初入职场" } }
  ] }
};

// —— 命名方式推断 ——
test("resolveNamingMode：模板含 englishTitle 字段块 → en", () => {
  assert.equal(resolveNamingMode({ templates: { movie: [{ type: "field", key: "englishTitle" }] } }), "en");
});
test("resolveNamingMode：仅中文/兼容字段块 → zh", () => {
  assert.equal(resolveNamingMode({ templates: {
    movie: [{ type: "field", key: "title" }, { type: "text", value: "abc" }],
    tv: [{ type: "field", key: "chineseTitle" }]
  } }), "zh");
});
test("resolveNamingMode：任意模板（含季目录）出现 englishTitle → en", () => {
  assert.equal(resolveNamingMode({ templates: { inPlaceSeasonFolder: [{ type: "field", key: "season" }, { type: "field", key: "englishTitle" }] } }), "en");
});
test("resolveNamingMode：空/缺失 config 兜底 zh", () => {
  assert.equal(resolveNamingMode(void 0), "zh");
  assert.equal(resolveNamingMode({}), "zh");
  assert.equal(resolveNamingMode({ templates: {} }), "zh");
});

// —— RC-1 回归：文件名英文不再进入英文别名 ——
test("enrichMediaTitleAliases 不再用文件名英文兜底 englishTitles", () => {
  const media = enrichMediaTitleAliases({ ...noEnglish });
  assert.ok(media);
  assert.ok(Array.isArray(media.englishTitles));
  assert.ok(!media.englishTitles.some((title) => /SomeShow|Workplace/i.test(title)), `英文别名不应含文件名英文：${media.englishTitles.join(" | ")}`);
});
test("TMDB 英文别名存在时原样保留且排序不受文件名影响", () => {
  const media = enrichMediaTitleAliases({ ...withEnglish });
  assert.deepEqual([...media.englishTitles], ["In the Mood for Love"]);
});

// —— RC-7 回归：details 里的 US 区域英文别名要全部收录，非英文地区的本地化标题不混入 ——
test("英文原名影片的 US 区域英文别名全部进 englishTitles，原名排首位", () => {
  const media = normalizeTmdbMedia({ ...minions3 });
  assert.equal(media.englishTitles[0], "Minions & Monsters");
  for (const title of ["Minions 3", "Minions and Monsters", "Illumination's Minions & Monsters"]) {
    assert.ok(media.englishTitles.includes(title), `缺少英文别名：${title}（实际：${media.englishTitles.join(" | ")}）`);
  }
  assert.ok(!media.englishTitles.some((title) => /Monstros|und Monster|Minionki/i.test(title)), `非英文别名混入：${media.englishTitles.join(" | ")}`);
});
test("titleAliasOptions en 对多英文别名完整返回", () => {
  const options = titleAliasOptions({ ...minions3 }, "en");
  assert.equal(options.length, 4, `应含 4 个英文别名，实际：${options.join(" | ")}`);
  assert.ok(options.includes("Minions 3"));
  assert.ok(!options.some((title) => /Monstros|und Monster|Minionki/i.test(title)), `非英文别名混入：${options.join(" | ")}`);
});

// —— RC-2 回归：被污染的 media.englishTitles 重新 normalize 后自愈 ——
test("normalizeTmdbMedia 不回收旧 englishTitles（污染自愈）", () => {
  const polluted = { ...withEnglish, englishTitles: ["SomeShow.2024.2160p.WEB-DL", "Polluted Title"] };
  const media = normalizeTmdbMedia(polluted);
  assert.ok(!media.englishTitles.some((title) => /SomeShow|Polluted/i.test(title)), `污染值被固化：${media.englishTitles.join(" | ")}`);
  assert.deepEqual([...media.englishTitles], ["In the Mood for Love"]);
});
test("无 TMDB 英文别名的污染对象 normalize 后英文别名为空", () => {
  const polluted = { ...noEnglish, englishTitles: ["Workplace.Newcomers.2025"] };
  const media = normalizeTmdbMedia(polluted);
  assert.deepEqual([...media.englishTitles], []);
});

// —— 下拉框语言选择与回退 ——
test("titleAliasOptions zh → 中文别名", () => {
  const options = titleAliasOptions({ ...withEnglish }, "zh");
  assert.ok(options.includes("花样年华"));
  assert.ok(!options.some((title) => /[A-Za-z]/.test(title) && !/[\u3400-\u9fff]/.test(title)));
});
test("titleAliasOptions en + 有英文别名 → 英文别名", () => {
  assert.deepEqual([...titleAliasOptions({ ...withEnglish }, "en")], ["In the Mood for Love"]);
});
test("titleAliasOptions en + 无英文别名 → 回退中文别名（RC-4）", () => {
  const options = titleAliasOptions({ ...noEnglish }, "en");
  assert.ok(options.length > 0, "英文别名缺失时应回退中文别名，不应返回空");
  assert.ok(options.includes("初入职场"));
});
test("titleAliasOptions zh + 无英文别名不受影响", () => {
  assert.ok(titleAliasOptions({ ...noEnglish }, "zh").includes("初入职场"));
});
test("titleAliasOptions：media 为空 → 空列表", () => {
  assert.deepEqual([...titleAliasOptions(null, "en")], []);
  assert.deepEqual([...titleAliasOptions(null, "zh")], []);
});

// —— applyTmdbFields 字段回填回退链（RC-6）——
test("applyTmdbFields：无英文别名时 namingTitle 回退中文别名", () => {
  const fields = applyTmdbFields({ title: "", namingTitle: "", year: "" }, { ...noEnglish });
  assert.equal(fields.title, "初入职场");
  assert.equal(fields.namingTitle, "初入职场");
});
test("applyTmdbFields：有英文别名时 namingTitle 用英文别名", () => {
  const fields = applyTmdbFields({ title: "", namingTitle: "", year: "" }, { ...withEnglish });
  assert.equal(fields.title, "花样年华");
  assert.equal(fields.namingTitle, "In the Mood for Love");
});

// —— 打分链路在干净别名数据上正常 ——
test("scoreTmdbCandidate：文件名英文标题能命中 TMDB 英文别名", () => {
  const media = normalizeTmdbMedia({ ...withEnglish });
  const score = scoreTmdbCandidate(media, { title: "In the Mood for Love", year: "2000", mediaType: "movie" });
  assert.ok(score >= 100, `英文别名应精确命中，得分 ${score}`);
});
test("scoreTmdbCandidate：中文标题命中中文别名", () => {
  const media = normalizeTmdbMedia({ ...noEnglish });
  const score = scoreTmdbCandidate(media, { title: "初入职场", year: "2025", mediaType: "tv", season: "1" });
  assert.ok(score >= 100, `中文别名应精确命中，得分 ${score}`);
});

await chain;
console.log(`\n${passed} passed`);
