// TMDB 助手回归测试：从 tmdb-helper.user.js 中切出纯逻辑模块，
// 在 Node 里驱动解析器、排期引擎、统一数据源、字段匹配与 payload 构建。用法：node 油猴脚本/tests/tmdb-helper.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

// 豆瓣桌面页 HTML 解析器需要 DOMParser：用 tests/ 本地的 jsdom 提供（回退旧托管路径）
const require2 = createRequire(import.meta.url);
let JSDOM;
try {
    ({ JSDOM } = require2("jsdom"));
} catch (err) {
    ({ JSDOM } = require2("/Users/wei/.workbuddy/binaries/node/workspace/node_modules/jsdom/lib/api.js"));
}
const DOMParser = new JSDOM("").window.DOMParser;

const scriptPath = new URL("../tmdb-helper.user.js", import.meta.url);
const lines = fs.readFileSync(scriptPath, "utf8").split("\n");
const slice = (fromMarker, toMarker) => {
    const start = lines.findIndex((line) => line.includes(fromMarker));
    const end = lines.findIndex((line) => line.includes(toMarker));
    if (start < 0 || end <= start) throw new Error(`bundle markers not found: ${fromMarker} .. ${toMarker}`);
    return lines.slice(start, end).join("\n");
};
// 注意：net→dom 区间跨 douban/tmdbapi 段，整片切出
const code = [slice("// src/config.js", "// src/parser.js"), slice("// src/parser.js", "// src/fields.js"), slice("// src/fields.js", "// src/datasource.js"), slice("// src/datasource.js", "// src/episodes.js"), slice("// src/episodes.js", "// src/net.js"), slice("// src/net.js", "// src/dom.js")].join("\n");
const driver = `;
globalThis.__api = {
    tmdbhNormalizeConfig,
    parseYearValue,
    normalizeAirDate,
    splitStructuredLine,
    parseEntryText,
    parseEpisodeText,
    extractEpisodeNumber,
    findDuplicateEpisodeNumbers,
    findMissingEpisodeNumbers,
    exportEpisodesToTsv,
    applyEpisodeFilterWords,
    mapTmdbSeasonEpisodes,
    epochToLocalDate,
    tmdbhSiteJsonp,
    cleanQqTitle,
    parseBilibiliUrl,
    extractIqiyiAlbumId,
    parseMgtvCollectionId,
    parseQqCoverCid,
    parseYoukuTarget,
    mapBilibiliEpisodes,
    mapIqiyiEpisodes,
    mapMgtvEpisodes,
    mapQqUnionEpisodes,
    mapYoukuVideos,
    upgradeIqiyiImageUrl,
    appendMgtvOssResize,
    tmdbhImageReferer,
    extractHongguoRouterData,
    parseHongguoSeries,
    mapHongguoSearchList,
    mapBilibiliSearchResults,
    matchSiteSource,
    buildEpisodeSchedule,
    tmdbhParseWeekdays,
    scoreDoubanSuggestion,
    normalizeForMatch,
    looksLatin,
    detectPageContext,
    tmdbhParsePageTitle,
    detectEditorLanguage,
    buildEpisodePayload,
    buildEpisodeUpdatePayload,
    buildRemoteEpisodeIndex,
    normalizeRemoteEpisodes,
    buildStillUploadFields,
    buildStillUploadFormFields: buildStillUploadFields,
    episodeStillsPageUrl,
    seasonImagesUrl,
    loadDoneEpisodes,
    recordDoneEpisodes,
    episodeGroupsRemoteUrl,
    episodeGroupSubGroupsUrl,
    episodeGroupSubGroupEpisodesUrl,
    buildEpisodeGroupWritePayload,
    buildSubGroupWritePayload,
    buildSubGroupEpisodesPayload,
    sortSubGroupEpisodes,
    filterSubGroupCandidates,
    parseImageUploadConfig,
    posterCropTarget,
    upgradeDoubanPosterUrl,
    TMDBH_IMAGE_SPECS,
    inferImageType,
    detectImageBlackBars,
    computeImageTransform,
    parseDoubanRexxarJson,
    tmdbhMatchDoubanSubjectId,
    tmdbhTmdbReady,
    TMDBH_GROUP_TYPES,
    createUnifiedRecord,
    toList,
    castToList,
    normalizeDoubanDetail,
    normalizeTmdbDetail,
    normalizeParsedEntry,
    unifiedToEntryValues,
    recordTitleYear,
    buildRecordText,
    tmdbhViewsForContext,
    createDataSourceRegistry,
    createEpisodeActionRegistry
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, Promise, URLSearchParams, URL, DOMParser };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "tmdb-helper.user.js" });
const api = sandbox.__api;
// vm 沙箱里的数组/对象与本模块原型不同，deepEqual 前先 JSON 往返
const js = (value) => JSON.parse(JSON.stringify(value));

// —— 日期归一化 ——
assert.equal(api.normalizeAirDate("2024-1-1"), "2024-01-01");
assert.equal(api.normalizeAirDate("2024.01.05"), "2024-01-05");
assert.equal(api.normalizeAirDate("2024年1月1日"), "2024-01-01");
assert.equal(api.normalizeAirDate("2024/1/31"), "2024-01-31");
assert.equal(api.normalizeAirDate("20240101"), "2024-01-01");
assert.equal(api.normalizeAirDate("2024-02-29"), "2024-02-29");
assert.equal(api.normalizeAirDate("2024-13-01"), "");
assert.equal(api.normalizeAirDate("2024-02-30"), "");
assert.equal(api.normalizeAirDate("2024-06-31"), "");
assert.equal(api.normalizeAirDate(""), "");
assert.equal(api.parseYearValue("流浪地球3 (2027)"), 2027);
assert.equal(api.parseYearValue("没有年份的文本"), 0);
assert.equal(api.parseYearValue("1993年首播"), 1993);

// —— 结构化行切分 ——
assert.deepEqual(js(api.splitStructuredLine("标题 | 2024 | 简介内容")), ["标题", "2024", "简介内容"]);
assert.deepEqual(js(api.splitStructuredLine("1\t第一集\t2024-01-01")), ["1", "第一集", "2024-01-01"]);
assert.deepEqual(js(api.splitStructuredLine("a｜b")), ["a", "b"]);
assert.equal(api.splitStructuredLine("普通一句话没有任何分隔"), null);

// —— 条目解析：管道行 ——
const piped = js(api.parseEntryText("盗梦空间 | Inception | 2010 | 一层深入梦境的科幻动作片，结构精巧值得反复观看"));
assert.equal(piped.title, "盗梦空间");
assert.equal(piped.originalTitle, "Inception");
assert.equal(piped.year, 2010);
assert.ok(piped.overview.includes("梦境"));

const pipedSimple = js(api.parseEntryText("肖申克的救赎 | 1994 | 希望让人自由的监狱题材经典之作"));
assert.equal(pipedSimple.title, "肖申克的救赎");
assert.equal(pipedSimple.year, 1994);
assert.ok(pipedSimple.overview.includes("希望"));
assert.equal(pipedSimple.originalTitle, "");

// —— 条目解析：标签行 + 多行简介 + 又名回填原名 ——
const labeled = js(api.parseEntryText([
    "标题：三体",
    "原名：The Three-Body Problem",
    "年份：2023",
    "首播：2023-01-15",
    "单集片长：45分钟",
    "又名：Three-body / 三体",
    "简介：刘慈欣小说改编",
    "剧情续写第二行",
    ""
].join("\n")));
assert.equal(labeled.title, "三体");
assert.equal(labeled.originalTitle, "The Three-Body Problem");
assert.equal(labeled.year, 2023);
assert.equal(labeled.date, "2023-01-15");
assert.equal(labeled.runtime, 45);
assert.equal(labeled.overview, "刘慈欣小说改编\n剧情续写第二行");

const aliasOnly = js(api.parseEntryText([
    "标题：花样年华",
    "又名：In the Mood for Love / 花样年华",
    "简介：王家卫执导的经典爱情电影，足够长的简介内容"
].join("\n")));
assert.equal(aliasOnly.title, "花样年华");
assert.equal(aliasOnly.originalTitle, "In the Mood for Love");

// —— 条目解析：独立标题行（标题 (2024) 写法）——
const standalone = js(api.parseEntryText("流浪地球3 (2027)\n这是后续行，会被并进简介"));
assert.equal(standalone.title, "流浪地球3");
assert.equal(standalone.year, 2027);
assert.ok(standalone.overview.includes("后续行"));

// 英文标签需要冒号，普通英文句子不会被误吃成标签值
const english = js(api.parseEntryText("title: Inception\noverview: A movie about dreams inside dreams, layered and clever."));
assert.equal(english.title, "Inception");
assert.ok(english.overview.includes("dreams"));

// —— 条目解析：标语/官网/IMDb/制作代码/导演/主演 等扩展标签 ——
const extended = js(api.parseEntryText([
    "标题：肖申克的救赎",
    "年份：1994",
    "标语：Hope is a good thing",
    "官方网站：https://example.com/shawshank",
    "IMDb：tt0111161",
    "制作代码：SC-1994-001",
    "导演：弗兰克·德拉邦特",
    "主演：蒂姆·罗宾斯 饰 安迪 / 摩根·弗里曼 饰 瑞德",
    "简介：银行家含冤入狱，用二十年凿开救赎之路。"
].join("\n")));
assert.equal(extended.tagline, "Hope is a good thing");
assert.equal(extended.homepage, "https://example.com/shawshank");
assert.equal(extended.imdb, "tt0111161");
assert.equal(extended.productionCode, "SC-1994-001");
assert.ok(extended.directors.includes("德拉邦特"));
assert.ok(extended.cast.includes("蒂姆·罗宾斯 饰 安迪"));

// —— 分集解析 ——
const tsv = js(api.parseEpisodeText([
    "# 这是注释，应被跳过",
    "1\t第一集\t2024-01-01\t开场简介文字",
    "2\t第二集\t2024-01-08",
    "// 另一种注释"
].join("\n")));
assert.equal(tsv.length, 2);
assert.deepEqual(tsv[0], { episodeNumber: 1, name: "第一集", airDate: "2024-01-01", overview: "开场简介文字", runtime: 0, stillUrl: "" });
assert.equal(tsv[1].airDate, "2024-01-08");
assert.equal(tsv[1].overview, "");

const freeLine = js(api.parseEpisodeText("第01集：开篇 2024年1月15日"));
assert.equal(freeLine.length, 1);
assert.equal(freeLine[0].episodeNumber, 1);
assert.equal(freeLine[0].name, "开篇");
assert.equal(freeLine[0].airDate, "2024-01-15");

const parenLine = js(api.parseEpisodeText("第10期：名字（2024.8.5）"));
assert.equal(parenLine[0].episodeNumber, 10);
assert.equal(parenLine[0].name, "名字");
assert.equal(parenLine[0].airDate, "2024-08-05");

const pipeLine = js(api.parseEpisodeText("S01E03|名字|2024.2.1"));
assert.equal(pipeLine[0].episodeNumber, 3);
assert.equal(pipeLine[0].name, "名字");
assert.equal(pipeLine[0].airDate, "2024-02-01");

const spaceLine = js(api.parseEpisodeText("01 第一集标题"));
assert.equal(spaceLine[0].episodeNumber, 1);
assert.equal(spaceLine[0].name, "第一集标题");

const yearLine = js(api.parseEpisodeText("1994 上映之后的事情"));
assert.equal(yearLine.length, 0);

const continuation = js(api.parseEpisodeText("1\t第一集\n这一行没有集数，会并进上一集的简介"));
assert.equal(continuation[0].overview, "这一行没有集数，会并进上一集的简介");

const sorted = js(api.parseEpisodeText("10\t第十集\n2\t第二集"));
assert.deepEqual(sorted.map((ep) => ep.episodeNumber), [2, 10]);

const runtimeEp = js(api.parseEpisodeText("5\t第五集\t45分钟\t2024-02-05"));
assert.equal(runtimeEp[0].runtime, 45);
assert.equal(runtimeEp[0].airDate, "2024-02-05");

// v1.1 分集行支持缩略图直链（URL 会被从标题里剥出来单独存）
const stillLine = js(api.parseEpisodeText("1\t第一集\t2024-01-01\thttps://img.example/s1.jpg"));
assert.equal(stillLine[0].stillUrl, "https://img.example/s1.jpg");
assert.equal(stillLine[0].name, "第一集");
const stillPiped = js(api.parseEpisodeText("2 | 第二集 | https://img.example/s2.jpg | 2024-01-08"));
assert.equal(stillPiped[0].stillUrl, "https://img.example/s2.jpg");
assert.equal(stillPiped[0].airDate, "2024-01-08");

// —— 分号分隔（兼容 TMDB-Import / tmdb-automation 的 episodes.txt） ——
const semi = js(api.parseEpisodeText("1;2011/12/4;45;国歌;备受爱戴的苏珊娜公主遭人绑架"));
assert.deepEqual(semi[0], { episodeNumber: 1, name: "国歌", airDate: "2011-12-04", overview: "备受爱戴的苏珊娜公主遭人绑架", runtime: 45, stillUrl: "" });
const semiCjk = js(api.parseEpisodeText("2；2011/12/11；六十二；一千五百万点；."));
assert.equal(semiCjk[0].episodeNumber, 2);
assert.equal(semiCjk[0].name, "六十二");
assert.equal(semiCjk[0].runtime, 0); // 中文数字时长不解析，数字（45 等）才解析
// 普通中文句子里的分号不会被误切成结构化行
assert.equal(api.parseEpisodeText("一部电影；讲述故事；很好看").length, 0);

// —— 豆瓣候选打分：精确标题+年份 稳赢包含匹配 ——
const subjects = [
    { title: "流浪地球", sub_title: "The Wandering Earth", year: "2019" },
    { title: "流浪地球2", sub_title: "The Wandering Earth II", year: "2023" }
];
const best = Math.max(...subjects.map((s) => api.scoreDoubanSuggestion(s, "流浪地球2", "2023")));
assert.equal(api.scoreDoubanSuggestion(subjects[1], "流浪地球2", "2023"), best);
assert.ok(api.scoreDoubanSuggestion(subjects[1], "流浪地球2", "2023") > api.scoreDoubanSuggestion(subjects[0], "流浪地球2", "2023"));
// 中文标点/空格不影响归一化比较
assert.equal(api.normalizeForMatch("流浪地球 2！"), api.normalizeForMatch("流浪地球2"));
assert.equal(api.looksLatin("The Shawshank Redemption"), true);
assert.equal(api.looksLatin("肖申克的救赎"), false);

// —— 页面上下文识别 ——
assert.deepEqual(js(api.detectPageContext("/tv/1399-game-of-thrones/season/1/edit")), { kind: "season-edit", mediaType: "tv", tvId: 1399, seasonNumber: 1 });
assert.deepEqual(js(api.detectPageContext("/tv/999-show/season/1/episode/4/edit")), { kind: "episode-edit", mediaType: "tv", tvId: 999, seasonNumber: 1, episodeNumber: 4 });
assert.deepEqual(js(api.detectPageContext("/tv/999-show/season/1/episode/4/images/backdrops")), { kind: "episode-images", mediaType: "tv", tvId: 999, seasonNumber: 1, episodeNumber: 4, imageType: "backdrops" });
assert.deepEqual(js(api.detectPageContext("/movie/550-fight-club/edit")), { kind: "movie-edit", mediaType: "movie", id: 550 });
assert.deepEqual(js(api.detectPageContext("/tv/1399/edit")), { kind: "tv-edit", mediaType: "tv", id: 1399 });
assert.deepEqual(js(api.detectPageContext("/movie/new")), { kind: "movie-new", mediaType: "movie" });
assert.deepEqual(js(api.detectPageContext("/tv/new/")), { kind: "tv-new", mediaType: "tv" });
assert.deepEqual(js(api.detectPageContext("/tv/1399-game-of-thrones")), { kind: "tv-detail", mediaType: "tv", id: 1399 });
assert.deepEqual(js(api.detectPageContext("/movie/550/")), { kind: "movie-detail", mediaType: "movie", id: 550 });
assert.equal(api.detectPageContext("/").kind, "other");

// —— 分集 payload 构建：空字段不应出现 ——
assert.deepEqual(
    js(api.buildEpisodePayload({ episodeNumber: 3, name: "名字", airDate: "2024-01-03", overview: "", runtime: 0 }, 2)),
    { episode_number: 3, locked_fields: [], season_number: 2, new: true, name: "名字", air_date: "2024-01-03" }
);
assert.deepEqual(
    js(api.buildEpisodePayload({ episodeNumber: 1, name: "", airDate: "", overview: "简介", runtime: 45 }, 1)),
    { episode_number: 1, locked_fields: [], season_number: 1, new: true, overview: "简介", runtime: 45 }
);

// —— 季编辑器语言探测 / 已有集归一化（v1.1 增加 bson_id 透传，供缩略图直传） ——
assert.equal(api.detectEditorLanguage("中文 (zh-CN)"), "zh-CN");
assert.equal(api.detectEditorLanguage("English (en-US)"), "en-US");
assert.equal(api.detectEditorLanguage("没有标记"), "");
assert.deepEqual(
    js(api.normalizeRemoteEpisodes([
        { id: 9, episode_number: 1, name: "a", air_date: "2024-01-01", overview: "第一集\n简介内容", runtime: "45", bson_id: "aa11" },
        { bad: "x" },
        null
    ])),
    [{ id: 9, episodeNumber: 1, name: "a", airDate: "2024-01-01", overview: "第一集 简介内容", runtime: 45, bsonId: "aa11" }]
);

// —— 配置归一化：默认值、钳制、开关 ——
const defaultConfig = js(api.tmdbhNormalizeConfig(null));
assert.equal(defaultConfig.douban.enabled, true);
assert.equal(defaultConfig.douban.minIntervalMs, 2000);
assert.equal(defaultConfig.episodes.delayMs, 1000);
assert.equal(defaultConfig.episodes.retries, 1);
assert.equal(defaultConfig.stills.delayMs, 1500);
assert.equal(defaultConfig.panel.x, null);
assert.equal(defaultConfig.tmdb.apiKey, "");
assert.equal(defaultConfig.filters.words.includes("PV"), true);
const patched = js(api.tmdbhNormalizeConfig({ douban: { enabled: false, minIntervalMs: 100 }, episodes: { delayMs: 99999, retries: 9 }, stills: { delayMs: 1 }, tmdb: { apiKey: "  abc1234567890  " } }));
assert.equal(patched.douban.enabled, false);
assert.equal(patched.douban.minIntervalMs, 500);
assert.equal(patched.episodes.delayMs, 60000);
assert.equal(patched.episodes.retries, 3);
assert.equal(patched.stills.delayMs, 1);
assert.equal(patched.tmdb.apiKey, "abc1234567890");
assert.equal(api.tmdbhTmdbReady({ tmdb: { apiKey: "" } }), false);
assert.equal(api.tmdbhTmdbReady({ tmdb: { apiKey: "short" } }), false);
assert.equal(api.tmdbhTmdbReady({ tmdb: { apiKey: "abc1234567890" } }), true);
assert.ok(api.TMDBH_GROUP_TYPES[1]);
assert.equal(api.TMDBH_GROUP_TYPES[6], "制作顺序");

// —— 剧集组内部接口：URL 与写 payload ——
assert.equal(api.episodeGroupsRemoteUrl(71446), "/tv/71446/remote/episode_groups?translate=false");
assert.equal(api.episodeGroupSubGroupsUrl(71446, "5eb730df"), "/tv/71446/remote/episode_group/5eb730df/groups?translate=false");
assert.equal(api.episodeGroupSubGroupEpisodesUrl(71446, "5eb730df", "62fe1fa5"), "/tv/71446/remote/episode_group/5eb730df/62fe1fa5/episodes");
assert.deepEqual(
    js(api.buildEpisodeGroupWritePayload({ name: " 流通版 ", description: " 描述 ", type: 3 }, false)),
    { name: "流通版", description: "描述", type: 3 }
);
const groupWrite = js(api.buildEpisodeGroupWritePayload({ name: "名", description: "", type: 99, id: "abc" }, true));
assert.equal(groupWrite.type, 2); // 越界类型回退默认 2
assert.equal(groupWrite.id, "abc");
assert.deepEqual(
    js(api.buildSubGroupWritePayload({ name: " Disc 1 " }, false, 4)),
    { name: "Disc 1", order: 4, episode_count: 0 }
);
assert.deepEqual(
    js(api.buildSubGroupWritePayload({ name: "Disc 2", order: -1, episode_count: "7", id: "s2" }, true, 0)),
    { name: "Disc 2", order: 0, episode_count: 7, id: "s2" }
);

// —— 子组单集成员：PUT models 包装载荷 / 排序 / 候选过滤 ——
const modelsPayload = js(api.buildSubGroupEpisodesPayload([
    { media_id: "abc", season_number: 1, episode_number: 2, name: "B", order: 7 },
    { bson_id: "def", season_number: 1, episode_number: 1, name: "A" }
]));
assert.deepEqual(modelsPayload, {
    models: [
        { media_id: "abc", season_number: 1, episode_number: 2, name: "B", order: 7 },
        { media_id: "def", season_number: 1, episode_number: 1, name: "A", order: 1 }
    ]
});
const sortedEps = js(api.sortSubGroupEpisodes([
    { media_id: "b", season_number: 2, episode_number: 1 },
    { media_id: "a", season_number: 1, episode_number: 3 },
    { media_id: "c", season_number: 1, episode_number: 1 }
]));
assert.deepEqual(sortedEps.map((e) => e.media_id), ["c", "a", "b"]);
assert.deepEqual(sortedEps.map((e) => e.order), [0, 1, 2]);
const candidates = js(api.filterSubGroupCandidates(
    [{ bson_id: "a" }, { media_id: "b" }, { bson_id: "" }, { media_id: "a" }],
    ["a"]
));
assert.deepEqual(candidates.map((e) => e.media_id || e.bson_id), ["b"]);

// —— 图片上传页配置解析 / 裁切比例 ——
const uploadCfg = js(api.parseImageUploadConfig(`
    $("#upload_files").kendoUpload({
        async: { saveUrl: '/image', autoUpload: true, dataType: 'json' },
        upload: function(e) {
            e.data = {
                media_id: '67004145b146282f7b852dda',
                media_type: 'TvSeason',
                type: 'poster',
                translate: false
            }
        }
    });
`));
assert.deepEqual(uploadCfg, { mediaId: "67004145b146282f7b852dda", mediaType: "TvSeason", type: "poster" });
assert.equal(api.parseImageUploadConfig("<html>no config</html>"), null);
assert.equal(api.parseImageUploadConfig("media_id: 'aabbccddeeff', type: 'unknown'").type, "poster"); // 非法类型回退 poster
// 页面上远处的 type: 'POST' 不能污染解析（配置窗口只看 media_id 邻近 400 字符）
assert.equal(
    api.parseImageUploadConfig("type: 'POST', dataType: 'json' ... media_id: 'aabbccddeeff', media_type: 'TvSeason', type: 'poster', translate: false").type,
    "poster"
);
// 单集剧照页：media_type=TvEpisode、type=still 也能解析
assert.deepEqual(
    js(api.parseImageUploadConfig("media_id: 'aabbccddeeff001122334455', media_type: 'TvEpisode', type: 'still', translate: false")),
    { mediaId: "aabbccddeeff001122334455", mediaType: "TvEpisode", type: "still" }
);
assert.equal(api.posterCropTarget("16:9"), 16 / 9);
assert.equal(api.posterCropTarget("2:3"), 2 / 3);
assert.equal(api.posterCropTarget(), 2 / 3);

// —— 豆瓣海报原图升级（m_ratio_poster 只有 ~480px 宽，raw 才能过 TMDB 最低分辨率） ——
assert.equal(
    api.upgradeDoubanPosterUrl("https://img1.doubanio.com/view/photo/m_ratio_poster/public/p2935292760.jpg"),
    "https://img1.doubanio.com/view/photo/raw/public/p2935292760.jpg"
);
assert.equal(
    api.upgradeDoubanPosterUrl("https://img2.doubanio.com/view/photo/s_ratio_poster/public/p123.webp"),
    "https://img2.doubanio.com/view/photo/raw/public/p123.webp"
);
assert.equal(
    api.upgradeDoubanPosterUrl("https://img1.doubanio.com/view/photo/raw/public/p2935292760.jpg"),
    "https://img1.doubanio.com/view/photo/raw/public/p2935292760.jpg"
);
assert.equal(api.upgradeDoubanPosterUrl("https://example.com/view/photo/m_ratio_poster/public/p1.jpg"), "https://example.com/view/photo/m_ratio_poster/public/p1.jpg");
assert.equal(api.upgradeDoubanPosterUrl(""), "");

// —— 图片类型规范推断与变换计算 ——
assert.equal(api.TMDBH_IMAGE_SPECS.poster.ratio, 2 / 3);
assert.equal(api.TMDBH_IMAGE_SPECS.logo.mime, "image/png");
assert.equal(api.TMDBH_IMAGE_SPECS.still.minWidth, 1280); // 剧照（分集缩略图）16:9 ≥1280×720
assert.equal(api.inferImageType(1000, 1500, false), "poster");
assert.equal(api.inferImageType(1920, 1080, false), "backdrop");
assert.equal(api.inferImageType(900, 300, true), "logo");
// 2000x3000 恰好合规：无裁剪无缩放
const okTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.poster, 2000, 3000));
assert.deepEqual(okTransform.crop, { sx: 0, sy: 0, sw: 2000, sh: 3000 });
assert.equal(okTransform.width, 2000);
assert.equal(okTransform.height, 3000);
assert.deepEqual(js(okTransform.notes), []);
assert.deepEqual(js(okTransform.problems), []);
const cropTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.poster, 1000, 800));
assert.equal(cropTransform.crop.sw, Math.round(800 * 2 / 3));
assert.ok(cropTransform.notes.some((n) => n.includes("2:3")));
assert.ok(cropTransform.crop.sx > 0);
// 过小：300x450 → TMDB 禁止放大小图，必须明确报错（不缩放）
const smallTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.poster, 300, 450));
assert.deepEqual(js(smallTransform.problems), ["分辨率 300×450 低于最低要求 500×750（TMDB 禁止放大低分辨率小图，请更换更清晰的图片）"]);
assert.deepEqual(js(smallTransform.notes), []);
// 过大：3000x4500 → 缩到上限内
const bigTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.poster, 3000, 4500));
assert.ok(bigTransform.width <= 2000 && bigTransform.height <= 3000);
assert.ok(bigTransform.notes.some((n) => n.includes("缩小")));
// logo 不自动裁比例；过小（160x40）必须明确报错（禁止放大）
const logoTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.logo, 160, 40));
assert.deepEqual(js(logoTransform.crop), { sx: 0, sy: 0, sw: 160, sh: 40 });
assert.ok(logoTransform.problems[0].includes("低于最低要求 200×50"));
// 剧照：2000x1000 偏宽 → 裁宽度到 16:9（sw=1778, sh 不变），过小报错
const stillTransform = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.still, 2000, 1000));
assert.equal(stillTransform.crop.sw, Math.round(1000 * 16 / 9));
assert.equal(stillTransform.crop.sh, 1000);
assert.ok(stillTransform.notes.some((n) => n.includes("16:9")));
const stillSmall = js(api.computeImageTransform(api.TMDBH_IMAGE_SPECS.still, 640, 360));
assert.ok(stillSmall.problems[0].includes("低于最低要求 1280×720"));

// —— v1.1 灵活排期引擎：星期解析 ——
assert.deepEqual(js(api.tmdbhParseWeekdays("一,四")), [1, 4]);
assert.deepEqual(js(api.tmdbhParseWeekdays("周一、周四")), [1, 4]);
assert.deepEqual(js(api.tmdbhParseWeekdays("星期日 0 7")), [0]);
assert.deepEqual(js(api.tmdbhParseWeekdays("周中")), [1, 2, 3, 4, 5]);
assert.deepEqual(js(api.tmdbhParseWeekdays("周末")), [0, 6]);
assert.deepEqual(js(api.tmdbhParseWeekdays("Mon Wed/FRI")), [1, 3, 5]);
assert.deepEqual(js(api.tmdbhParseWeekdays("")), []);
assert.deepEqual(js(api.tmdbhParseWeekdays("随便写")), []);

// —— v1.1 灵活排期引擎：固定间隔（原周播生成器泛化） ——
const weekly = js(api.buildEpisodeSchedule({ pattern: "interval", startNumber: 2, count: 3, startDate: "2024-03-30", intervalDays: 7, titleTemplate: "第{n}期" }));
assert.deepEqual(weekly.episodes.map((ep) => [ep.episodeNumber, ep.airDate, ep.name]), [[2, "2024-03-30", "第2期"], [3, "2024-04-06", "第3期"], [4, "2024-04-13", "第4期"]]);
assert.ok(weekly.error === "" && weekly.meta.firstDate === "2024-03-30");
assert.ok(!api.buildEpisodeSchedule({ pattern: "interval", count: 3, startDate: "bad-date" }).episodes.length);
assert.ok(!api.buildEpisodeSchedule({ pattern: "interval", count: 0, startDate: "2024-01-01" }).episodes.length);
const weeklyDefaultTemplate = js(api.buildEpisodeSchedule({ pattern: "interval", startNumber: 1, count: 1, startDate: "2024-01-01" }));
assert.equal(weeklyDefaultTemplate.episodes[0].name, "第1集");

// —— v1.1 灵活排期引擎：每周固定更新日 + 单日多集（动漫周一/周四各更两集） ——
const anime = js(api.buildEpisodeSchedule({ pattern: "weekly", startNumber: 1, count: 6, perSlot: 2, startDate: "2026-01-05", weekdays: "一,四" }));
assert.deepEqual(
    anime.episodes.map((ep) => [ep.episodeNumber, ep.airDate]),
    [[1, "2026-01-05"], [2, "2026-01-05"], [3, "2026-01-08"], [4, "2026-01-08"], [5, "2026-01-12"], [6, "2026-01-12"]],
    "同日多集共用日期、集号连续"
);
assert.ok(anime.meta.summary.includes("周一") && anime.meta.summary.includes("周四"));
assert.ok(anime.meta.summary.includes("每次 2 集"));
// 首播日不是更新日：顺延到下一个更新日
const shifted = js(api.buildEpisodeSchedule({ pattern: "weekly", startNumber: 1, count: 2, perSlot: 1, startDate: "2026-01-05", weekdays: "四" }));
assert.deepEqual(shifted.episodes.map((ep) => ep.airDate), ["2026-01-08", "2026-01-15"]);
// 缺更新日明确报错
assert.ok(api.buildEpisodeSchedule({ pattern: "weekly", count: 2, startDate: "2026-01-05", weekdays: "" }).error.includes("更新日"));
// {k} 日内序号与片长透传
const perSlotTpl = js(api.buildEpisodeSchedule({ pattern: "weekly", startNumber: 3, count: 2, perSlot: 2, startDate: "2026-02-02", weekdays: "一", titleTemplate: "第{n}集({k})", runtime: 24 }));
assert.deepEqual(perSlotTpl.episodes.map((ep) => [ep.name, ep.runtime]), [["第3集(1)", 24], ["第4集(2)", 24]]);
// {i} 全局序号
const globalTpl = js(api.buildEpisodeSchedule({ pattern: "interval", startNumber: 1, count: 2, perSlot: 2, startDate: "2026-02-01", intervalDays: 1, titleTemplate: "E{i}-{n}" }));
assert.deepEqual(globalTpl.episodes.map((ep) => ep.name), ["E1-1", "E2-2"]);
// 集数上限 500（钳制），count 缺失报错文案
assert.equal(api.buildEpisodeSchedule({ pattern: "interval", count: 9999, startDate: "2026-01-01" }).episodes.length, 500);
assert.ok(api.buildEpisodeSchedule({ pattern: "interval", startDate: "2026-01-01" }).error.includes("集数"));

// —— v1.1 重复集检测 ——
assert.deepEqual(
    js(api.findDuplicateEpisodeNumbers([{ episodeNumber: 1 }, { episodeNumber: 2 }, { episodeNumber: 2 }, { episodeNumber: 3 }, { episodeNumber: 2 }])),
    [2]
);
assert.deepEqual(js(api.findDuplicateEpisodeNumbers([{ episodeNumber: 1 }, { episodeNumber: 2 }])), []);

// —— 缺集检测 / TSV 导出（v1.1 增加缩略图列，可直接回贴解析框） ——
assert.deepEqual(
    js(api.findMissingEpisodeNumbers([{ episodeNumber: 1 }, { episodeNumber: 2 }, { episodeNumber: 5 }])),
    [3, 4]
);
assert.deepEqual(js(api.findMissingEpisodeNumbers([{ episodeNumber: 3 }])), []);
assert.deepEqual(js(api.findMissingEpisodeNumbers([])), []);
assert.equal(
    api.exportEpisodesToTsv([{ episodeNumber: 1, name: "第一集", airDate: "2024-01-01", runtime: 45, overview: "简介\n第二行", stillUrl: "" }]),
    "1\t第一集\t2024-01-01\t45\t简介 第二行\t"
);
assert.equal(
    api.exportEpisodesToTsv([{ episodeNumber: 2, name: "第二集", airDate: "2024-01-08", runtime: 0, overview: "", stillUrl: "https://img.example/s2.jpg" }]),
    "2\t第二集\t2024-01-08\t\t\thttps://img.example/s2.jpg"
);

// —— TMDB-Import 式过滤词：命中标题剔除 + 重编号 + 保留原有缺集 ——
{
    const eps = [
        { episodeNumber: 1, name: "开端" },
        { episodeNumber: 2, name: "PV" },
        { episodeNumber: 3, name: "第二集" },
        { episodeNumber: 5, name: "第四集" } // 原有缺集 4
    ];
    const result = js(api.applyEpisodeFilterWords(eps, "pv, 预告"));
    assert.deepEqual(result.episodes.map((e) => e.episodeNumber), [1, 2, 4]); // PV 行剔除后重编号，缺集 4 保留跳档
    assert.deepEqual(result.episodes.map((e) => e.name), ["开端", "第二集", "第四集"]);
    assert.deepEqual(result.removed.map((e) => e.name), ["PV"]);
    // 大小写不敏感 + 中英逗号混排
    const caseHit = js(api.applyEpisodeFilterWords([{ episodeNumber: 1, name: "NCOP 无字幕" }, { episodeNumber: 2, name: "正片" }], "ncop，花絮"));
    assert.deepEqual(caseHit.episodes.map((e) => e.episodeNumber), [1]);
    assert.deepEqual(caseHit.removed.map((e) => e.name), ["NCOP 无字幕"]);
    // 无命中：原样返回
    const untouched = js(api.applyEpisodeFilterWords(eps, "OVA"));
    assert.deepEqual(untouched.episodes, eps);
    assert.deepEqual(untouched.removed, []);
    // 空词表：原样返回
    const noWords = js(api.applyEpisodeFilterWords(eps, ""));
    assert.deepEqual(noWords.episodes, eps);
    // 起始集号非 1 时按原基准重编号：[5,6(PV),8] → [5,6,8]（缺集 7 保留）
    const offset = js(api.applyEpisodeFilterWords([
        { episodeNumber: 5, name: "一" },
        { episodeNumber: 6, name: "预告片" },
        { episodeNumber: 8, name: "三" }
    ], "预告"));
    assert.deepEqual(offset.episodes.map((e) => e.episodeNumber), [5, 7]);
}

// —— tmdb-scraper 式：TMDB 官方季详情 → 分集表格行 ——
{
    const rows = js(api.mapTmdbSeasonEpisodes({ episodes: [
        { episode_number: 1, name: "第一集", air_date: "2024-1-1", runtime: 45, overview: "第一行\n第二行", still_path: "/a.jpg" },
        { episode_number: "2", name: "  第二集  ", air_date: "", runtime: 0, overview: "", still_path: "" },
        { episode_number: 0, name: "没有集号" }
    ] }));
    assert.deepEqual(rows[0], { episodeNumber: 1, name: "第一集", airDate: "2024-01-01", runtime: 45, overview: "第一行 第二行", stillUrl: "https://image.tmdb.org/t/p/original/a.jpg" });
    assert.deepEqual(rows[1], { episodeNumber: 2, name: "第二集", airDate: "", runtime: 0, overview: "", stillUrl: "" });
    assert.equal(rows.length, 2);
    assert.deepEqual(js(api.mapTmdbSeasonEpisodes(null)), []);
    assert.deepEqual(js(api.mapTmdbSeasonEpisodes({})), []);
}

// —— 平台抓取器（TMDB-Import 免浏览器提取器移植）：URL 解析与分集映射 ——
{
    // B站 URL：ss / ep / md 三种形态
    assert.deepEqual(js(api.parseBilibiliUrl("https://www.bilibili.com/bangumi/play/ss26575")), { type: "ss", id: "26575" });
    assert.deepEqual(js(api.parseBilibiliUrl("https://www.bilibili.com/bangumi/play/ep262142")), { type: "ep", id: "262142" });
    assert.deepEqual(js(api.parseBilibiliUrl("https://www.bilibili.com/bangumi/media/md28234541")), { type: "md", id: "28234541" });
    assert.equal(api.parseBilibiliUrl("https://example.com/video"), null);
    // B站分集映射：跳过预告、纯数字 title 作集号、（上/下）拼接长标题、pubtime → 本地日期、毫秒时长 → 分钟
    const bili = js(api.mapBilibiliEpisodes({
        title: "示例番剧",
        episodes: [
            { title: "1", long_title: "开端", pub_time: 1700000000, duration: 1440000, cover: "https://i0.hdslb.com/1.jpg" },
            { title: "2", long_title: "预告篇", badge: "预告", duration: 60000 },
            { title: "3（上）", long_title: "决战", pub_time: "", release_date: "2024-01-15", duration: 1800000 },
            { title: "特别篇", long_title: "幕后", duration: 900000 }
        ]
    }));
    assert.equal(bili.length, 3); // 预告被跳过
    assert.deepEqual(bili[0], { episodeNumber: 1, name: "开端", airDate: "2023-11-15", runtime: 24, overview: "", stillUrl: "https://i0.hdslb.com/1.jpg" });
    assert.deepEqual(bili[1], { episodeNumber: 2, name: "3（上） 决战", airDate: "2024-01-15", runtime: 30, overview: "", stillUrl: "" });
    assert.equal(bili[2].episodeNumber, 3, "预告剔除后集号连续递增（提交目标是 TMDB 集号）");
    assert.equal(bili[2].name, "幕后");
    // B站是内置抓取器
    assert.equal(api.matchSiteSource("https://www.bilibili.com/bangumi/play/ss26575").id, "bilibili");

    // 爱奇艺：专辑 ID 三种埋点 + epsodelist 映射
    assert.equal(api.extractIqiyiAlbumId("https://www.iqiyi.com/a_1fkgtbddd2x.html", '<div data-album-id="5328486914190101">'), "5328486914190101");
    assert.equal(api.extractIqiyiAlbumId("https://www.iqiyi.com/lib/m_4466.html", 'movlibalbumaid="44661586"'), "44661586");
    assert.equal(api.extractIqiyiAlbumId("https://www.iqiyi.com/v_xkt6.html", '"albumId":532848,"channelId":1'), "532848");
    const iqiyi = js(api.mapIqiyiEpisodes([
        { order: 1, subtitle: "指导组入驻京海调查", period: "2023-01-14", duration: "45:33", description: "第一行\n第二行", imageUrl: "http://pic0.iqiyipic.com/image/2025/d0/8b/v_170963922_m_601_m11.jpg", imageSize: ["480_360", "160_90", "1248_702", "1080_608"] },
        { order: 2, name: "第二集", period: "", duration: "1:02:05", description: "" }
    ]));
    assert.deepEqual(iqiyi[0], { episodeNumber: 1, name: "指导组入驻京海调查", airDate: "2023-01-14", runtime: 45, overview: "第一行 第二行", stillUrl: "http://pic0.iqiyipic.com/image/2025/d0/8b/v_170963922_m_601_m11_1248_702.jpg" });
    assert.equal(iqiyi[1].runtime, 62, "HH:MM:SS 时长换算分钟");
    // 爱奇艺封面升级：imageSize 里挑面积最大档追加进 URL；无 imageSize 时原样返回
    assert.equal(api.upgradeIqiyiImageUrl("http://pic0.iqiyipic.com/a/v_1_m.jpg", ["480_360", "1248_702", "1080_608"]), "http://pic0.iqiyipic.com/a/v_1_m_1248_702.jpg");
    assert.equal(api.upgradeIqiyiImageUrl("http://pic0.iqiyipic.com/a/v_1_m.jpg", []), "http://pic0.iqiyipic.com/a/v_1_m.jpg");

    // 芒果TV：collection_id 取路径首个数字段；isIntact=1 才收；img 去尺寸后缀
    assert.equal(api.parseMgtvCollectionId("https://w.mgtv.com/b/419629/17004788.html"), "419629");
    assert.equal(api.parseMgtvCollectionId("https://www.mgtv.com/h/390933.html"), "390933");
    const mgtv = js(api.mapMgtvEpisodes([
        { isIntact: "1", t1: "1", t2: "第一集", ts: "2022-08-02 11:50:10", time: "45:00", img: "https://3img.hitv.com/preview/sp/20220720195240412_1280x720.jpg" },
        { isIntact: "0", t1: "2", t2: "预告", ts: "2022-08-02", time: "1:00", img: "https://3img.hitv.com/preview/sp/2.jpg" }
    ]));
    assert.deepEqual(mgtv, [{ episodeNumber: 1, name: "第一集", airDate: "2022-08-02", runtime: 45, overview: "", stillUrl: "https://3img.hitv.com/preview/sp/20220720195240412?x-oss-process=image/resize,w_1280" }]);
    // 芒果 OSS resize 参数：hitv.com 域名追加，其他域名原样
    assert.equal(api.appendMgtvOssResize("https://3img.hitv.com/a/b.jpg"), "https://3img.hitv.com/a/b.jpg?x-oss-process=image/resize,w_1280");
    assert.equal(api.appendMgtvOssResize("https://img.example.com/a.jpg"), "https://img.example.com/a.jpg");
    // 图片床防盗链 Referer 映射
    assert.equal(api.tmdbhImageReferer("https://i0.hdslb.com/bfs/a.jpg"), "https://www.bilibili.com/");
    assert.equal(api.tmdbhImageReferer("https://pic0.iqiyipic.com/a.jpg"), "https://www.iqiyi.com/");
    assert.equal(api.tmdbhImageReferer("https://img.example.com/a.jpg"), "");
    assert.equal(api.matchSiteSource("https://www.mgtv.com/b/419629/17004788.html").id, "mgtv");

    // 腾讯视频：cid 解析、JSONP 剥壳、标题清洗、正片过滤、集号回退、封面升级
    assert.equal(api.parseQqCoverCid("https://v.qq.com/x/cover/mzc00200t0fg7k8/o0043eaefxx.html"), "mzc00200t0fg7k8");
    assert.equal(api.tmdbhSiteJsonp('QZOutputJson={"a":1};'), '{"a":1}');
    assert.equal(api.cleanQqTitle("我的二分之一男友_01"), "我的二分之一男友");
    assert.equal(api.cleanQqTitle("第12集 走向和解"), "走向和解");
    const qq = js(api.mapQqUnionEpisodes([
        { fields: { category_map: [11812, "横屏正片", 2, "电视剧"], episode: "01", title: "示例剧_01", second_title: null, video_checkup_time: "2022-08-02 11:50:10", duration: "305", pic160x90: "http://puui.qpic.cn/vpic_cover/o0043/o0043_hz.jpg/160" } },
        { fields: { category_map: [11813, "预告片"], episode: "02", title: "预告_02", duration: "60" } },
        { fields: { category_map: ["正片", 2], episode: "", title: "加更篇", second_title: "加更：幕后", video_checkup_time: "", duration: "0" } }
    ].map((item) => item.fields)));
    assert.equal(qq.length, 2); // 预告片类别被过滤
    assert.deepEqual(qq[0], { episodeNumber: 1, name: "示例剧", airDate: "2022-08-02", runtime: 5, overview: "", stillUrl: "http://puui.qpic.cn/vpic_cover/o0043/o0043_hz.jpg/1280" });
    assert.equal(qq[1].episodeNumber, 2, "episode 为 0/空时顺序回退");
    assert.equal(qq[1].name, "加更：幕后");
    assert.equal(api.matchSiteSource("https://v.qq.com/x/cover/mzc00200t0fg7k8.html").id, "qq");

    // 优酷：s= / vid= / id_ 三种目标形态；videos 列表映射（seq 集号 + 小写 bigthumbnail，实测字段）
    assert.deepEqual(js(api.parseYoukuTarget("https://v.youku.com/v_show/id_XNTkxMzg==.html")), { type: "video", id: "XNTkxMzg" });
    assert.deepEqual(js(api.parseYoukuTarget("https://www.youku.com/show/id_6463d199.html?s=efbfbd3f")) , { type: "show", id: "efbfbd3f" });
    const youku = js(api.mapYoukuVideos([
        { seq: "3", rc_title: "薛芳菲遭构陷雨夜活埋", title: "墨雨云间 03", published: "2024-06-02 11:45:10", duration: "2894.68", description: "", bigthumbnail: "https://m.ykimg.com/1.jpg" },
        { title: "第四集", published: "", duration: "", description: "" }
    ]));
    assert.deepEqual(youku[0], { episodeNumber: 3, name: "薛芳菲遭构陷雨夜活埋", airDate: "2024-06-02", runtime: 48, overview: "", stillUrl: "https://m.ykimg.com/1.jpg" });
    assert.equal(youku[1].episodeNumber, 2, "无 seq 时顺序回退");
    assert.equal(youku[1].name, "第四集");
    assert.equal(api.matchSiteSource("https://v.youku.com/v_show/id_XNTkxMzg==.html").id, "youku");
    // 不支持的链接
    assert.equal(api.matchSiteSource("https://www.netflix.com/watch/123"), null);
    assert.equal(api.matchSiteSource("不是链接"), null);
}

// —— 红果短剧（hongguoduanju.com SSR）：_ROUTER_DATA 提取、剧集详情、站内搜索映射 ——
{
    // fixture 模拟真实页面：字符串里带花括号与转义引号，考验括号配对
    const routerHtml = (seriesDetail, searchList) => `<html><script>window._ROUTER_DATA = ${JSON.stringify({
        loaderData: {
            detail_page: seriesDetail ? { seriesDetail } : null,
            "search_(keyword)/page": searchList ? { query: "好雨知时节", totalCount: 91, searchList } : null
        }
    }).replace("</script>", "<\\/script>")};</script></html>`;

    const detail = api.parseHongguoSeries(routerHtml({
        series_id: "7673056752958458942",
        series_name: "好雨知时节",
        series_intro: "两个被背叛的陌生人相遇的故事",
        series_cover: "https://p3-novel.byteimg.com/novel-pic/x~tplv-shrink:640:0.image",
        episode_cnt: 89,
        vid_list: ["7673058582404795417", "7673058620795259929", "7673058641750019097"]
    }));
    assert.equal(detail.title, "好雨知时节");
    assert.equal(detail.episodeCnt, 89);
    assert.equal(detail.episodes.length, 3, "按 vid_list 生成集行");
    assert.deepEqual(js(detail.episodes.map((e) => e.episodeNumber)), [1, 2, 3]);
    assert.equal(detail.episodes[0].name, "", "短剧分集无独立标题");
    assert.equal(detail.overview.includes("双向") || detail.overview.length > 0, true);
    assert.ok(detail.cover.includes("byteimg.com"));
    // 结构变化时明确报错
    assert.throws(() => api.parseHongguoSeries("<html>没有数据</html>"), /_ROUTER_DATA/);
    assert.throws(() => api.parseHongguoSeries(routerHtml(null, null)), /剧集详情/);

    // 站内搜索映射：doc_type/video_data → 统一候选行（带平台标记）
    const cands = js(api.mapHongguoSearchList([
        { doc_type: 23, name: "好雨知时节", keyword: "7673056752958458942", video_data: { series_id: "7673056752958458942", series_title: "好雨知时节", episode_cnt: 89, series_intro: "简介", series_cover: "https://p6-novel.byteimg.com/c.jpg" } },
        { doc_type: 1, name: "演员条目", keyword: "abc" }, // 无 video_data → 过滤
        { name: "无 video_data" }
    ]));
    assert.deepEqual(cands, [{ platform: "hongguo", platformName: "红果短剧", id: "7673056752958458942", title: "好雨知时节", episodeCnt: 89, intro: "简介", cover: "https://p6-novel.byteimg.com/c.jpg" }]);
    // B站番剧搜索映射：<em> 高亮剥壳、season_id 过滤
    const biliCands = js(api.mapBilibiliSearchResults({ code: 0, data: { result: [
        { season_id: 28747, title: '<em class="keyword">凡人修仙传</em>', desc: "韩立跑路", cover: "https://i0.hdslb.com/b.jpg", eps: [{ id: 1 }, { id: 2 }] },
        { title: "没有 season_id", desc: "" }
    ] } }));
    assert.deepEqual(biliCands, [{ platform: "bilibili", platformName: "哔哩哔哩", id: "28747", title: "凡人修仙传", episodeCnt: 2, intro: "韩立跑路", cover: "https://i0.hdslb.com/b.jpg" }]);
    assert.deepEqual(js(api.mapBilibiliSearchResults(null)), []);
    // 红果是内置抓取器（player 与 detail 两种链接形态）
    assert.equal(api.matchSiteSource("https://hongguoduanju.com/player/7673056752958458942").id, "hongguo");
    assert.equal(api.matchSiteSource("https://hongguoduanju.com/detail?series_id=7673056752958458942").id, "hongguo");
}

// —— 断点续传存储 ——
const memStore = new Map();
const stubStorage = { get: (key) => (memStore.has(key) ? memStore.get(key) : null), set: (key, value) => memStore.set(key, value) };
assert.equal(api.loadDoneEpisodes(stubStorage, 1, 1).size, 0);
api.recordDoneEpisodes(stubStorage, 1, 1, [1, 2]);
api.recordDoneEpisodes(stubStorage, 1, 1, [2, 3]);
assert.deepEqual(js(Array.from(api.loadDoneEpisodes(stubStorage, 1, 1))), [1, 2, 3]);
assert.equal(api.loadDoneEpisodes(stubStorage, 1, 2).size, 0);
stubStorage.set("Tmdb.Helper.EpisodesDone", "{bad json");
assert.equal(api.loadDoneEpisodes(stubStorage, 1, 1).size, 0);

// —— 主题归一化 ——
assert.equal(api.tmdbhNormalizeConfig({ theme: "dark" }).theme, "dark");
assert.equal(api.tmdbhNormalizeConfig({ theme: "bogus" }).theme, "auto");
assert.equal(api.tmdbhNormalizeConfig(null).theme, "auto");

// —— 标签解析新增：类型 / 制片国家 ——
const labeledV2 = js(api.parseEntryText([
    "标题：测试剧",
    "类型：剧情/悬疑",
    "制片国家/地区：中国大陆",
    "简介：这是一段足够长的简介内容用于校验类型与国家字段的解析"
].join("\n")));
assert.equal(labeledV2.genres, "剧情/悬疑");
assert.equal(labeledV2.countries, "中国大陆");

// —— 覆盖已有集：更新 payload 必须带 id/bson_id 等元数据且不含 new 标记 ——
const updatePayload = js(api.buildEpisodeUpdatePayload(
    { episodeNumber: 1, name: "新标题", airDate: "2026-08-15", overview: "", runtime: "" },
    1399,
    { id: 42, season_number: 1, bson_id: "abc123", production_code: "", vote_average: 7.5, vote_count: 3, name: "旧标题", overview: "旧简介", air_date: "2026-08-14", runtime: 30 }
));
assert.deepEqual(updatePayload, {
    episode_number: 1,
    locked_fields: [],
    id: 42,
    season_number: 1,
    show_id: 1399,
    bson_id: "abc123",
    production_code: "",
    vote_average: 7.5,
    vote_count: 3,
    name: "新标题",
    overview: "旧简介",
    air_date: "2026-08-15",
    runtime: 30
});
// 新值缺省时回退线上现值；current 缺失时给出安全默认
const fallbackPayload = js(api.buildEpisodeUpdatePayload({ episodeNumber: 2 }, 1399, {}));
assert.equal(fallbackPayload.name, "");
assert.equal(fallbackPayload.runtime, null);
assert.equal(fallbackPayload.show_id, 1399);

// —— 已有集原始响应索引：按集号取回原始对象（含 bson_id） ——
const index = api.buildRemoteEpisodeIndex([{ episode_number: 6, id: 9, bson_id: "zz" }, null, { episode_number: 1, id: 3 }]);
assert.equal(index[6].bson_id, "zz");
assert.equal(index[1].id, 3);
assert.equal(index[2], undefined);

// —— 页面上下文：剧集组查看/编辑页先于通用 /edit 规则 ——
assert.deepEqual(
    js(api.detectPageContext("/tv/91097-ling-long/edit/episode_group/62fe1f4975110d007cca7598")),
    { kind: "episode-group-edit", mediaType: "tv", tvId: 91097, groupId: "62fe1f4975110d007cca7598" }
);
assert.deepEqual(
    js(api.detectPageContext("/tv/91097/episode_group/5eb730dfca7ec6001f7beb51")),
    { kind: "episode-group-view", mediaType: "tv", tvId: 91097, groupId: "5eb730dfca7ec6001f7beb51" }
);
assert.equal(api.detectPageContext("/tv/1399/edit").kind, "tv-edit");

// —— rexxar 移动端接口 JSON 解析（豆瓣详情主路，防风控） ——
const rexxar = js(api.parseDoubanRexxarJson({
    title: "香辣江湖擂台",
    original_title: "",
    aka: ["Spicy Arena", "香辣江湖"],
    year: "2026",
    pubdate: ["2026-08-15(中国大陆)", "2026-09-01(中国台湾)"],
    genres: ["纪录片"],
    countries: ["中国大陆"],
    durations: ["30分钟"],
    episodes_count: "6",
    intro: "第一行简介\n第二行简介",
    rating: { value: 8.64 }
}, "38626252"));
assert.equal(rexxar.title, "香辣江湖擂台");
assert.equal(rexxar.year, 2026);
assert.equal(rexxar.date, "2026-08-15");
assert.equal(rexxar.genres, "纪录片");
assert.equal(rexxar.countries, "中国大陆");
assert.equal(rexxar.runtime, 30);
assert.equal(rexxar.episodeCount, 6);
assert.equal(rexxar.originalTitle, "Spicy Arena");
assert.equal(rexxar.rating, "8.6");
assert.ok(rexxar.overview.includes("第二行"));
assert.equal(rexxar.doubanUrl, "https://movie.douban.com/subject/38626252/");

// rexxar 人名数组（对象形态或字符串形态）→ 导演/编剧/主演字符串
const rexxarPeople = js(api.parseDoubanRexxarJson({
    title: "有人名的剧",
    directors: [{ name: "导演甲" }, { name: "导演乙" }],
    actors: ["主演甲", { name: "主演乙" }]
}, "1"));
assert.equal(rexxarPeople.directors, "导演甲/导演乙");
assert.equal(rexxarPeople.cast, "主演甲/主演乙");

const rexxarPoster = js(api.parseDoubanRexxarJson({ title: "有海报", pic: { large: "https://img1.doubanio.com/l.jpg", normal: "https://img1.doubanio.com/n.jpg" } }, "1"));
assert.equal(rexxarPoster.poster, "https://img1.doubanio.com/l.jpg");
const rexxarPosterFallback = js(api.parseDoubanRexxarJson({ title: "兜底海报", image: "https://img1.doubanio.com/i.jpg" }, "1"));
assert.equal(rexxarPosterFallback.poster, "https://img1.doubanio.com/i.jpg");

// 电影形态：original_title 直接用、无 pubdate/时长/评分也能稳
const rexxarMovie = js(api.parseDoubanRexxarJson({
    title: "盗梦空间",
    original_title: "Inception",
    year: "2010",
    pubdate: ["2010-09-01(中国大陆)"],
    genres: ["剧情", "科幻"],
    countries: ["美国", "英国"],
    durations: ["148分钟"]
}, "35414107"));
assert.equal(rexxarMovie.originalTitle, "Inception");
assert.equal(rexxarMovie.date, "2010-09-01");
assert.equal(rexxarMovie.genres, "剧情/科幻");
assert.equal(rexxarMovie.countries, "美国/英国");
assert.equal(rexxarMovie.rating, "");

// 无标题 / 非对象 → null（触发 HTML 兜底），空对象各字段取默认值
assert.equal(api.parseDoubanRexxarJson(null, "1"), null);
assert.equal(api.parseDoubanRexxarJson({ title: "" }, "1"), null);
const rexxarMinimal = js(api.parseDoubanRexxarJson({ title: "测试" }, "1"));
assert.equal(rexxarMinimal.date, "");
assert.equal(rexxarMinimal.runtime, 0);
assert.equal(rexxarMinimal.originalTitle, "");
assert.equal(rexxarMinimal.year, 0);

// —— IMDb 反查：明文 URL 与 douban.com/link2 百分号编码 URL 都能提取条目 ID ——
assert.equal(api.tmdbhMatchDoubanSubjectId('href="https://movie.douban.com/subject/1292000/"'), "1292000");
assert.equal(api.tmdbhMatchDoubanSubjectId("url=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F1292000%2F&amp;query=tt0137523"), "1292000");
assert.equal(api.tmdbhMatchDoubanSubjectId("https://movie.douban.com/subject/38626252/"), "38626252");
assert.equal(api.tmdbhMatchDoubanSubjectId("页面上没有条目链接"), "");

// —— v1.1 统一数据源抽象：统一条目记录 ——
const emptyRecord = js(api.createUnifiedRecord());
assert.deepEqual(
    Object.keys(emptyRecord).sort(),
    ["aliases", "cast", "companies", "countries", "date", "directors", "episodeCount", "genres", "languages", "networks", "originalTitle", "overview", "poster", "rating", "runtime", "source", "sourceId", "title", "url", "writers", "year"].sort()
);
assert.equal(api.toList("剧情/科幻、悬疑").length, 3);
assert.deepEqual(js(api.castToList("张三 饰 甲 / 李四 / 王五:配角")), [
    { name: "张三", character: "甲" },
    { name: "李四", character: "" },
    { name: "王五", character: "配角" }
]);
assert.deepEqual(js(api.castToList([{ name: "六", character: "嘉" }])), [{ name: "六", character: "嘉" }]);

// 豆瓣详情 → 统一记录
const doubanRecord = js(api.normalizeDoubanDetail({
    doubanId: "38626252",
    title: "香辣江湖擂台",
    originalTitle: "Spicy Arena",
    year: 2026,
    date: "2026-08-15",
    runtime: 30,
    overview: "简介内容",
    genres: "纪录片/真人秀",
    countries: "中国大陆",
    rating: "8.6",
    episodeCount: 6,
    directors: "导演甲",
    cast: "主演甲 / 主演乙 饰 乙",
    poster: "https://img1.doubanio.com/l.jpg"
}));
assert.equal(doubanRecord.source, "douban");
assert.equal(doubanRecord.sourceId, "38626252");
assert.equal(doubanRecord.url, "https://movie.douban.com/subject/38626252/");
assert.deepEqual(js(doubanRecord.genres), ["纪录片", "真人秀"]);
assert.deepEqual(js(doubanRecord.cast), [{ name: "主演甲", character: "" }, { name: "主演乙", character: "乙" }]);
assert.equal(doubanRecord.year, 2026);

// TMDB 详情 → 统一记录（movie + credits）
const tmdbRecord = js(api.normalizeTmdbDetail({
    id: 550,
    title: "搏击俱乐部",
    original_title: "Fight Club",
    release_date: "1999-10-15",
    runtime: 139,
    overview: "概述",
    genres: [{ name: "剧情" }],
    production_countries: [{ name: "美国" }],
    production_companies: [{ name: "公司甲" }],
    spoken_languages: [{ english_name: "English" }],
    vote_average: 8.4,
    poster_path: "/p.jpg",
    credits: {
        cast: [{ name: "演甲", character: "A" }],
        crew: [{ name: "导甲", job: "Director" }, { name: "编甲", job: "Writer" }, { name: "编乙", department: "Writing" }]
    }
}, "movie"));
assert.equal(tmdbRecord.source, "tmdb");
assert.equal(tmdbRecord.title, "搏击俱乐部");
assert.equal(tmdbRecord.year, 1999);
assert.equal(tmdbRecord.rating, "8.4");
assert.deepEqual(js(tmdbRecord.directors), ["导甲"]);
assert.equal(tmdbRecord.writers.length, 2);
assert.deepEqual(js(tmdbRecord.cast), [{ name: "演甲", character: "A" }]);
assert.equal(tmdbRecord.poster, "https://image.tmdb.org/t/p/w342/p.jpg");

// 粘贴文本 → 统一记录
const textRecord = js(api.normalizeParsedEntry({ title: "标题", year: 2024, genres: "剧情/科幻" }));
assert.equal(textRecord.source, "text");
assert.deepEqual(js(textRecord.genres), ["剧情", "科幻"]);

// 统一记录 → 填写条目值（类型拼 / 分隔；非法 imdb 剔除；豆瓣/TMDB 链接不当 homepage）
const entryValues = js(api.unifiedToEntryValues(doubanRecord));
assert.equal(entryValues.genres, "纪录片/真人秀");
assert.equal(entryValues.countries, "中国大陆");
assert.equal(entryValues.date, "2026-08-15");
assert.equal(entryValues.homepage, "");
assert.equal(entryValues.imdb, "");
const entryImdb = js(api.unifiedToEntryValues({ imdb: "tt0137523", homepage: "https://example.com", url: "https://example.com" }));
assert.equal(entryImdb.imdb, "TT0137523");
assert.equal(entryImdb.homepage, "https://example.com");

// —— v1.1 可插拔数据源注册表 ——
const registry = api.createDataSourceRegistry();
assert.throws(() => registry.register({ name: "缺 id" }), /缺少 id/);
registry.register({ id: "a", name: "A" });
registry.register({ id: "b", name: "B" });
assert.equal(registry.get("a").name, "A");
assert.equal(registry.get("zz"), null);
assert.deepEqual(js(registry.list().map((a) => a.id)), ["a", "b"]);

// —— v1.1 批量操作注册表（集编辑器扩展接口） ——
const actions = api.createEpisodeActionRegistry();
assert.throws(() => actions.register({ id: "bad" }), /缺少 id 或 run/);
const calls = [];
actions.register({ id: "x", title: "X", run: async () => { calls.push("x"); return "done"; } });
actions.register({ id: "only-when-true", title: "Y", when: (env) => env.ok, run: async () => "" });
assert.deepEqual(js(actions.list({ ok: true }).map((a) => a.id)), ["x", "only-when-true"]);
assert.deepEqual(js(actions.list({ ok: false }).map((a) => a.id)), ["x"]);
// 同 id 重注册 = 覆盖
actions.register({ id: "x", title: "X2", run: async () => { calls.push("x2"); return ""; } });
await actions.run("x", {});
assert.deepEqual(js(calls), ["x2"]);
await assert.rejects(() => actions.run("missing", {}), /未注册的批量操作/);

// —— v1.1 分集缩略图直传契约与单集剧照页 URL ——
assert.deepEqual(js(api.buildStillUploadFields("bisson123")), { media_id: "bisson123", media_type: "TvEpisode", type: "still", translate: "false" });
assert.equal(api.episodeStillsPageUrl(1399, 1, 3), "/tv/1399/season/1/episode/3/images/backdrops");
assert.equal(api.seasonImagesUrl(1399, 2), "/tv/1399/season/2/images/posters");

// —— v1.1.1 页面标题解析：详情页「(TV Series 2026)」形态也能剥出年份（复制 tmdbid 不再丢年份） ——
assert.deepEqual(js(api.tmdbhParsePageTitle("香辣江湖擂台 (TV Series 2026) — The Movie Database (TMDB)")), { title: "香辣江湖擂台", year: 2026 });
assert.deepEqual(js(api.tmdbhParsePageTitle("搏击俱乐部 (1999) — The Movie Database (TMDB)")), { title: "搏击俱乐部", year: 1999 });
assert.deepEqual(js(api.tmdbhParsePageTitle("权力的游戏 (TV 2011)")), { title: "权力的游戏", year: 2011 });
assert.deepEqual(js(api.tmdbhParsePageTitle("没有年份的标题")), { title: "没有年份的标题", year: 0 });
assert.deepEqual(js(api.tmdbhParsePageTitle("")), { title: "", year: 0 });

// —— v1.2.1 复制格式：名称 (年份) 与「复制全部信息」 ——
assert.equal(api.recordTitleYear({ title: "香辣江湖擂台", year: 2026 }), "香辣江湖擂台 (2026)");
assert.equal(api.recordTitleYear({ title: "香辣江湖擂台", originalTitle: "Spicy Arena", year: 2026 }, true), "Spicy Arena (2026)");
assert.equal(api.recordTitleYear({ title: "没有年份", year: 0, date: "" }), "没有年份");
assert.equal(api.recordTitleYear({ title: "兜底日期取年", year: 0, date: "2024-03-01" }), "兜底日期取年 (2024)");
const recordText = api.buildRecordText(doubanRecord);
assert.ok(recordText.includes("标题：香辣江湖擂台"));
assert.ok(recordText.includes("年份：2026"));
assert.ok(recordText.includes("主演：主演甲、主演乙 饰 乙"));
assert.ok(!recordText.includes("IMDb："), "空字段不应产出空行");
const recordTextEmpty = api.buildRecordText({ title: "只有标题" });
assert.equal(recordTextEmpty, "标题：只有标题");

// —— v1.2.1 视图映射：默认只搜索、剧集组只在剧集组页、上传只在图片页 ——
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "movie-new" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "tv-new" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "movie-edit" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "tv-edit" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "episode-edit" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "movie-detail" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "tv-detail" })), ["search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "season-edit" })), ["episodes", "search"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "episode-group-view" })), ["groups"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "episode-group-edit" })), ["groups"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "images" })), ["upload"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "season-images" })), ["upload"]);
assert.deepEqual(js(api.tmdbhViewsForContext({ kind: "episode-images" })), ["upload"]);
assert.equal(api.tmdbhViewsForContext({ kind: "other" }), null);

// —— 封面左右半裁剪：横向拼图封面取单面（纵向裁剪不受影响） ——
const posterSpec = api.TMDBH_IMAGE_SPECS.poster;
const rightCrop = api.computeImageTransform(posterSpec, 1600, 1074, { cropHalf: "right" });
assert.equal(rightCrop.crop.sw, 716);
assert.equal(rightCrop.crop.sx, 884);
assert.ok(rightCrop.notes.some((note) => note.includes("右半")), "右半裁剪应写进处理说明");
const leftCrop = api.computeImageTransform(posterSpec, 1600, 1074, { cropHalf: "left" });
assert.equal(leftCrop.crop.sx, 0);
const centerCrop = api.computeImageTransform(posterSpec, 1600, 1074);
assert.equal(centerCrop.crop.sx, Math.round((1600 - 716) / 2));
const tallCrop = api.computeImageTransform(posterSpec, 800, 1400, { cropHalf: "right" });
assert.equal(tallCrop.crop.sh, 1200);
assert.equal(tallCrop.crop.sy, 100);
const wideCrop = api.computeImageTransform(api.TMDBH_IMAGE_SPECS.backdrop, 1600, 900, { cropHalf: "right" });
assert.equal(wideCrop.crop.sx, 0, "16:9 图按 16:9 规范无需裁剪");

// —— 黑边检测（TMDB-Import bordercrop 思路）：上下黑边识别、小边忽略、全暗图受裁切上限保护 ——
{
    // 160×90：上下各 10 行纯黑，中间纯白；两侧无黑边
    const px = new Uint8ClampedArray(160 * 90 * 4);
    for (let y = 0; y < 90; y++) {
        for (let x = 0; x < 160; x++) {
            const i = (y * 160 + x) * 4;
            const v = y >= 10 && y < 80 ? 255 : 0;
            px[i] = px[i + 1] = px[i + 2] = v;
            px[i + 3] = 255;
        }
    }
    const bars = js(api.detectImageBlackBars(px, 160, 90));
    assert.deepEqual(bars, { top: 10, bottom: 10, left: 0, right: 0 });
    // 黑边不足 minBar（<4px）忽略
    const thin = new Uint8ClampedArray(160 * 90 * 4);
    for (let y = 0; y < 90; y++) {
        for (let x = 0; x < 160; x++) {
            const i = (y * 160 + x) * 4;
            const v = y >= 2 && y < 88 ? 255 : 0;
            thin[i] = thin[i + 1] = thin[i + 2] = v;
            thin[i + 3] = 255;
        }
    }
    const thinBars = js(api.detectImageBlackBars(thin, 160, 90));
    assert.deepEqual(thinBars, { top: 0, bottom: 0, left: 0, right: 0 });
    // 全黑图：扫描被 45% 上限截断，不会裁光
    const allBlack = new Uint8ClampedArray(160 * 90 * 4); // 全 0 即全黑
    const capped = js(api.detectImageBlackBars(allBlack, 160, 90));
    assert.ok(capped.top <= 41 && capped.bottom <= 41, "单边裁切不应超过 45% 上限");
    // 非黑内容行正常截停：左右黑柱
    const cols = new Uint8ClampedArray(160 * 90 * 4);
    for (let y = 0; y < 90; y++) {
        for (let x = 0; x < 160; x++) {
            const i = (y * 160 + x) * 4;
            const v = x >= 20 && x < 140 ? 255 : 0;
            cols[i] = cols[i + 1] = cols[i + 2] = v;
            cols[i + 3] = 255;
        }
    }
    const colBars = js(api.detectImageBlackBars(cols, 160, 90));
    assert.deepEqual(colBars, { top: 0, bottom: 0, left: 20, right: 20 });
    // 无像素数据时返回全 0
    assert.deepEqual(js(api.detectImageBlackBars(null, 160, 90)), { top: 0, bottom: 0, left: 0, right: 0 });
}

console.log("tmdb-helper.test.mjs：全部断言通过 ✓");
