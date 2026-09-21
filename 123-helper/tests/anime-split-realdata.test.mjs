// 动漫拆分·真实分季数据链测试：Bangumi 客户端（反代优先/官方回退/UA）、
// CureTMDb 社区分季表加载与缓存、loadBangumiStructureForMedia 端到端串链。
// 用法：node 油猴脚本/123-helper/tests/anime-split-realdata.test.mjs
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
  slice("// src/tmdb.js", "// src/metadata.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");

const gmStore = new Map();
const fetchCalls = [];
let routes = [];
const jsonResponse = (status = 200, payload = {}) => ({ ok: status >= 200 && status < 300, status, json: async () => payload, text: async () => JSON.stringify(payload) });
const fakeFetch = async (url, options = {}) => {
  const target = String(url);
  fetchCalls.push({ url: target, method: String(options.method || "GET").toUpperCase(), options });
  for (const route of routes) {
    if (route.method && route.method !== String(options.method || "GET").toUpperCase()) continue;
    if (!route.match.test(target)) continue;
    return jsonResponse(route.status, typeof route.body === "function" ? route.body(target, options) : route.body);
  }
  return jsonResponse(500, {});
};

const driver = `;
globalThis.checkpointStorageGet = (key) => { try { return GM_getValue(key, null); } catch { return null; } };
globalThis.checkpointStorageSet = (key, value) => { try { GM_setValue(key, value); } catch { } };
globalThis.checkpointStorageRemove = (key) => { try { GM_deleteValue ? GM_deleteValue(key) : void 0; } catch { } };
globalThis.__anime = {
  BangumiClient,
  bangumiClient,
  bangumiClientCacheClear: () => bangumiClient.cache.clear(),
  loadCureTmdbTvMap,
  loadCureTmdbTvStructure,
  loadBangumiStructureForMedia,
  activeBangumiHost: () => bangumiActiveHost,
  BANGUMI_API_HOSTS,
  CURE_TMDB_TV_JSON_URL,
  CURE_TMDB_TV_CACHE_KEY,
  BANGUMI_STRUCTURE_CACHE_PREFIX
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, Promise, fetch: fakeFetch, GM_getValue: (key, fallback = null) => (gmStore.has(key) ? gmStore.get(key) : fallback), GM_setValue: (key, value) => { gmStore.set(key, value); } };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { BangumiClient, bangumiClient, bangumiClientCacheClear, loadCureTmdbTvMap, loadCureTmdbTvStructure, loadBangumiStructureForMedia, activeBangumiHost, BANGUMI_API_HOSTS, CURE_TMDB_TV_JSON_URL, CURE_TMDB_TV_CACHE_KEY, BANGUMI_STRUCTURE_CACHE_PREFIX } = sandbox.__anime;

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

const SAMPLE_TV_JSON = {
  "223564": { name: "超超超超超喜欢你的100个女朋友", seasons: [{ season_number: 1, name: "第一季", episode_count: 12 }, { season_number: 2, name: "第二季", episode_count: 12 }] },
  "95479": { name: "咒术回战", seasons: [{ season_number: 1, name: "咒术回战", episode_count: 24 }, { season_number: 2, name: "涩谷事变", episode_count: 23 }] }
};
const SUBJECT_294993 = { id: 294993, type: 2, platform: "TV", name: "呪術廻戦", name_cn: "咒术回战", date: "2020-10-02", eps: 24, total_episodes: 25 };
const SUBJECT_369304 = { id: 369304, type: 2, platform: "TV", name: "呪術廻戦 懐玉・玉折", name_cn: "咒术回战 怀玉·玉折 / 涩谷事变", date: "2023-07-06", eps: 23, total_episodes: 24 };
const JUJUTSU_MEDIA = { id: 95479, mediaType: "tv", title: "咒术回战", originalTitle: "呪術廻戦", chineseTitles: ["咒术回战"], year: "2020" };
const installJujutsuRoutes = () => {
  routes = [
    { method: "GET", match: /raw\.githubusercontent\.com\/wikrin\/CureTMDb\/main\/tv\.json/, body: SAMPLE_TV_JSON },
    { method: "POST", match: /\/v0\/search\/subjects/, body: { data: [{ id: 294993, type: 2, platform: "TV", name: "呪術廻戦", name_cn: "咒术回战", date: "2020-10-02" }] } },
    { method: "GET", match: /\/v0\/subjects\/294993\/subjects/, body: [{ id: 369304, type: 2, relation: "续集", name: "呪術廻戦 懐玉・玉折", name_cn: "咒术回战 怀玉·玉折 / 涩谷事变", date: "2023-07-06" }] },
    { method: "GET", match: /\/v0\/subjects\/369304\/subjects/, body: [] },
    { method: "GET", match: /\/v0\/subjects\/294993$/, body: SUBJECT_294993 },
    { method: "GET", match: /\/v0\/subjects\/369304$/, body: SUBJECT_369304 }
  ];
};

test("BangumiClient：默认走反代，请求带 UA，搜索用 POST /v0/search/subjects", async () => {
  gmStore.clear();
  fetchCalls.length = 0;
  routes = [{ method: "POST", match: /\/v0\/search\/subjects/, body: { data: [SUBJECT_294993] } }];
  const results = await bangumiClient.searchSubjects("咒术回战");
  assert.equal(results.length, 1);
  assert.equal(activeBangumiHost(), BANGUMI_API_HOSTS[0]);
  const call = fetchCalls.find((item) => item.url.includes("/v0/search/subjects"));
  assert.equal(call.method, "POST");
  assert.equal(call.options.body, JSON.stringify({ keyword: "咒术回战" }));
  assert.equal(call.options.headers["User-Agent"].includes("123-helper"), true);
});

test("BangumiClient：反代故障自动回退官方域名", async () => {
  gmStore.clear();
  fetchCalls.length = 0;
  routes = [
    { method: "POST", match: new RegExp(`${BANGUMI_API_HOSTS[0].replace(/\./g, "\\.")}.*\\/v0\\/search\\/subjects`), status: 500, body: {} },
    { method: "POST", match: /\/v0\/search\/subjects/, body: { data: [] } }
  ];
  const results = await bangumiClient.searchSubjects("任何关键词");
  assert.deepEqual(plain(results), []);
  const hosts = fetchCalls.filter((item) => item.url.includes("/v0/search/subjects")).map((item) => new URL(item.url).origin);
  assert.deepEqual(hosts, [BANGUMI_API_HOSTS[0], BANGUMI_API_HOSTS[1]]);
  assert.equal(activeBangumiHost(), BANGUMI_API_HOSTS[1]);
});

test("loadCureTmdbTvStructure：拉社区表解析结构，24h 内走缓存", async () => {
  gmStore.clear();
  fetchCalls.length = 0;
  routes = [{ method: "GET", match: /raw\.githubusercontent\.com\/wikrin\/CureTMDb\/main\/tv\.json/, body: SAMPLE_TV_JSON }];
  const structure = await loadCureTmdbTvStructure(95479);
  assert.equal(structure.source, "curetmdb");
  assert.equal(structure.name, "咒术回战");
  assert.deepEqual(plain(structure.seasons.map((season) => [season.season, season.count])), [[1, 24], [2, 23]]);
  const githubCalls = () => fetchCalls.filter((item) => item.url === CURE_TMDB_TV_JSON_URL).length;
  assert.equal(githubCalls(), 1);
  // 第二次读缓存，不再发请求
  await loadCureTmdbTvStructure(95479);
  assert.equal(githubCalls(), 1);
  // 表里没有的 id → null
  assert.equal(await loadCureTmdbTvStructure(999999), null);
});

test("loadCureTmdbTvMap：网络失败报错且不写缓存", async () => {
  gmStore.clear();
  routes = [{ method: "GET", match: /raw\.githubusercontent\.com/, status: 503, body: {} }];
  await assert.rejects(loadCureTmdbTvMap());
  assert.equal(gmStore.has(CURE_TMDB_TV_CACHE_KEY), false);
});

test("loadBangumiStructureForMedia：搜索→匹配→续集链→期结构，端到端", async () => {
  gmStore.clear();
  bangumiClientCacheClear();
  fetchCalls.length = 0;
  installJujutsuRoutes();
  const structure = await loadBangumiStructureForMedia(JUJUTSU_MEDIA);
  assert.equal(structure.source, "bangumi");
  assert.equal(structure.name, "咒术回战");
  assert.deepEqual(plain(structure.seasons.map((season) => [season.season, season.count])), [[1, 24], [2, 23]]);
  assert.equal(structure.incomplete, false);
  // 结构落 GM 缓存（带时间戳）
  const cached = gmStore.get(`${BANGUMI_STRUCTURE_CACHE_PREFIX}95479`);
  assert.equal(cached?.value?.source, "bangumi");
  // 第二次直接命中缓存，不再发搜索请求
  const searchCalls = () => fetchCalls.filter((item) => item.url.includes("/v0/search/subjects")).length;
  assert.equal(searchCalls(), 1);
  const again = await loadBangumiStructureForMedia({ ...JUJUTSU_MEDIA, title: "另一个名字" });
  assert.equal(again.source, "bangumi");
  assert.equal(searchCalls(), 1);
});

test("loadBangumiStructureForMedia：匹配失败与网络失败分别返回 null / 抛错", async () => {
  gmStore.clear();
  bangumiClientCacheClear();
  routes = [{ method: "POST", match: /\/v0\/search\/subjects/, body: { data: [] } }];
  assert.equal(await loadBangumiStructureForMedia(JUJUTSU_MEDIA), null);
  bangumiClientCacheClear();
  routes = [{ method: "POST", match: /\/v0\/search\/subjects/, status: 500, body: {} }];
  await assert.rejects(loadBangumiStructureForMedia(JUJUTSU_MEDIA));
});

test("loadBangumiStructureForMedia：期数未知（连载中）标记 incomplete 并写入结构（WEB 平台网盘番同链路）", async () => {
  gmStore.clear();
  bangumiClientCacheClear();
  routes = [
    { method: "POST", match: /\/v0\/search\/subjects/, body: { data: [{ id: 500, type: 2, platform: "WEB", name: "新番", name_cn: "新番测试", date: "2026-01-08" }] } },
    { method: "GET", match: /\/v0\/subjects\/500\/subjects/, body: [{ id: 501, type: 2, relation: "续集", name: "新番 第二季", name_cn: "新番测试 第二季", date: "2027-01-08" }] },
    { method: "GET", match: /\/v0\/subjects\/501\/subjects/, body: [] },
    { method: "GET", match: /\/v0\/subjects\/500$/, body: { id: 500, type: 2, platform: "WEB", name: "新番", name_cn: "新番测试", date: "2026-01-08", eps: 12, total_episodes: 12 } },
    { method: "GET", match: /\/v0\/subjects\/501$/, body: { id: 501, type: 2, platform: "WEB", name: "新番 第二季", name_cn: "新番测试 第二季", date: "2027-01-08", eps: 0, total_episodes: 0 } }
  ];
  const structure = await loadBangumiStructureForMedia({ id: 500, mediaType: "tv", title: "新番测试", year: "2026" });
  assert.equal(structure.source, "bangumi");
  assert.deepEqual(plain(structure.seasons.map((season) => [season.season, season.count])), [[1, 12], [2, 0]]);
  assert.equal(structure.incomplete, true);
});

await chain;
console.log(`\n动漫拆分真实数据测试通过：${passed} 项`);
