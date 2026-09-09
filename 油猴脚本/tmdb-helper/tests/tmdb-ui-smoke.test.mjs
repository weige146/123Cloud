// tmdb-helper UI 冒烟测试：jsdom 里引导面板，遍历各页面上下文与视图，确认「搜索 + 复制」布局与关键交互不抛错。
// v1.2.1 起面板只查资料不回写页面：默认视图 = 搜索；批量单集只在季编辑器、剧集组只在剧集组页、上传只在图片页。
// 用法：node 油猴脚本/tests/tmdb-ui-smoke.test.mjs
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import vm from "node:vm";
import assert from "node:assert/strict";

// jsdom 优先用 tests/ 本地 node_modules，回退旧托管路径
const require = createRequire(import.meta.url);
let JSDOM;
try {
    ({ JSDOM } = require("jsdom"));
} catch (err) {
    ({ JSDOM } = require("/Users/wei/.workbuddy/binaries/node/workspace/node_modules/jsdom/lib/api.js"));
}

const scriptPath = new URL("../tmdb-helper.user.js", import.meta.url);
const code = fs.readFileSync(scriptPath, "utf8");

async function boot(pathname, { withApiKey = true, seed = {}, html = "", title = "", routes = [] } = {}) {
    const dom = new JSDOM(`<!doctype html><html><head><title>${title}</title></head><body>${html || '<div id="content"></div>'}</body></html>`, {
        url: `https://www.themoviedb.org${pathname}`,
        pretendToBeVisual: true
    });
    const { window } = dom;
    // jsdom 的 confirm 返回 undefined（视为取消）；写入流程测试默认放行
    window.confirm = () => true;
    // jsdom 里 offsetParent 恒为 null，会让表单控件采集全部被过滤；补丁模拟可见布局
    Object.defineProperty(window.HTMLElement.prototype, "offsetParent", {
        get() { return window.document.body; }
    });
    // 剪贴板桩：记录 writeText 内容，供复制类断言使用
    const clipboardWrites = [];
    Object.defineProperty(window.navigator, "clipboard", {
        value: {
            writeText: (text) => { clipboardWrites.push(text); return Promise.resolve(); },
            readText: () => Promise.resolve("")
        },
        configurable: true
    });
    const store = new Map();
    const route = (url) => routes.find((r) => (typeof r.match === "function" ? r.match(url) : r.match instanceof RegExp ? r.match.test(url) : String(url).startsWith(r.match)));
    const sandbox = {
        console,
        setTimeout, clearTimeout, setInterval, clearInterval,
        URL, URLSearchParams, DataTransfer: window.DataTransfer, File: window.File, Blob: window.Blob,
        DOMParser: window.DOMParser,
        sessionStorage: window.sessionStorage,
        localStorage: window.localStorage,
        FormData: window.FormData,
        createImageBitmap: undefined,
        GM_info: { script: { version: "1.2.1-test" } },
        GM_getValue: (k) => (store.has(k) ? store.get(k) : null),
        GM_setValue: (k, v) => store.set(k, v),
        GM_deleteValue: (k) => store.delete(k),
        GM_registerMenuCommand: () => {},
        GM_xmlhttpRequest: (options) => {
            const hit = route(options.url);
            setTimeout(() => {
                if (!hit) {
                    // 网络一律失败：面板应兜底报错而不是崩溃
                    options.onerror && options.onerror(new Error("offline"));
                    return;
                }
                const body = typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body);
                options.onload && options.onload({ status: hit.status || 200, responseText: body, finalUrl: options.url });
            }, 0);
        },
        fetch: (url, init) => {
            const hit = route(String(url));
            if (!hit || hit.fetch === false) return Promise.reject(new Error(`offline: ${url}`));
            const body = typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body);
            return Promise.resolve({
                ok: true,
                status: 200,
                text: () => Promise.resolve(body)
            });
        }
    };
    sandbox.window = window;
    sandbox.document = window.document;
    sandbox.location = window.location;
    sandbox.navigator = window.navigator;
    sandbox.Event = window.Event;
    sandbox.KeyboardEvent = window.KeyboardEvent;
    sandbox.MouseEvent = window.MouseEvent;
    sandbox.HTMLInputElement = window.HTMLInputElement;
    sandbox.HTMLTextAreaElement = window.HTMLTextAreaElement;
    sandbox.HTMLSelectElement = window.HTMLSelectElement;
    sandbox.Element = window.Element;
    sandbox.HTMLElement = window.HTMLElement;
    sandbox.CSS = window.CSS;
    sandbox.requestAnimationFrame = (fn) => setTimeout(fn, 0);
    sandbox.matchMedia = () => ({ matches: false, addEventListener: () => {} });
    sandbox.window.matchMedia = sandbox.matchMedia;
    if (withApiKey) {
        store.set("Tmdb.Helper.Config", JSON.stringify({ tmdb: { apiKey: "test-key-1234567890" } }));
    }
    for (const [key, value] of Object.entries(seed)) store.set(key, value);
    window.document.body.appendChild(window.document.createElement("div"));
    vm.createContext(sandbox);
    try {
        vm.runInContext(code, sandbox, { filename: "tmdb-helper.user.js" });
    } catch (err) {
        assert.fail(`脚本引导失败（${pathname}）：${err.stack || err.message}`);
    }
    // jsdom 的 DOMContentLoaded 是异步派发的，脚本会延迟挂载，先等一拍再取悬浮球
    await new Promise((resolve) => setTimeout(resolve, 20));
    const host = window.document.querySelector('[data-tmdb-helper="root"]');
    const shadow = host ? host.shadowRoot : null;
    return { dom, window, store, host, shadow, clipboardWrites };
}

const flush = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

// 页面适配：未适配页面不注入悬浮窗
{
    const { host } = await boot("/");
    await flush();
    assert.ok(!host, "未适配页面不应注入悬浮窗");
}

// —— 各页面上下文：悬浮球 + 浮层外壳 + 视图页签（默认只有搜索；专项视图只在对应页面） ——
const pageExpectations = [
    ["/tv/1399-game-of-thrones/season/1/edit", ["episodes", "search"]],
    ["/tv/1399-game-of-thrones/season/1/episode/3/edit", ["search"]],
    ["/tv/1399/edit", ["search"]],
    ["/movie/550-fight-club/edit", ["search"]],
    ["/movie/new", ["search"]],
    ["/tv/new", ["search"]],
    ["/tv/1399-game-of-thrones", ["search"]],
    ["/movie/550/", ["search"]],
    ["/tv/91097/episode_group/5eb730dfca7ec6001f7beb51", ["groups"]],
    ["/tv/91097/edit/episode_group/62fe1f4975110d007cca7598", ["groups"]],
    ["/tv/273499/season/1/images/posters", ["upload"]],
    ["/movie/550/images/backdrops", ["upload"]],
    ["/tv/1399/season/1/episode/1/images/backdrops", ["upload"]],
    ["/tv/1399-game-of-thrones/season/1", ["search"]]
];

for (const [pathname, expectedViews] of pageExpectations) {
    const { shadow } = await boot(pathname);
    await flush();
    assert.ok(shadow.querySelector(".tmdbh-ball"), `${pathname}: 悬浮球缺失`);
    assert.ok(shadow.querySelector(".tmdbh-window"), `${pathname}: 浮层窗口缺失`);
    const tabButtons = Array.from(shadow.querySelectorAll('[data-action="view"]'));
    const actualViews = tabButtons.map((btn) => btn.dataset.view);
    if (expectedViews.length > 1) {
        assert.deepEqual(actualViews, expectedViews, `${pathname}: 视图页签不符`);
    } else {
        assert.equal(tabButtons.length, 0, `${pathname}: 单视图页不应显示页签栏`);
    }
    const body = shadow.querySelector('[data-role="body"]');
    assert.ok(body && body.innerHTML.length > 40, `${pathname}: 默认视图渲染为空`);
    assert.ok(!shadow.querySelector('[data-role="overlay"]').classList.contains("open"), `${pathname}: 浮层不应自动弹出`);
}

// —— 只点击才显示：刷新后浮层保持收起，点悬浮球才打开；编辑页没有任何写入按钮 ——
{
    const { window, shadow } = await boot("/movie/550-fight-club/edit");
    await flush();
    const overlay = shadow.querySelector('[data-role="overlay"]');
    assert.ok(!overlay.classList.contains("open"), "加载后浮层应保持收起（绝不自动弹出）");
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(overlay.classList.contains("open"), "点击悬浮球后浮层应打开");
    assert.ok(shadow.querySelector('[data-role="source-query"]'), "搜索视图应有查询输入框");
    assert.ok(!shadow.querySelector(".tmdbh-compare-table"), "不应再出现对照填写表");
    assert.ok(!shadow.querySelector('[data-action^="compare-write"]'), "不应再有对照写入按钮");
    assert.ok(!shadow.querySelector('[data-action="bind-start"]'), "不应再有字段绑定入口");
    assert.ok(!shadow.querySelector('[data-action="harvest"]'), "不应再有「重新读取页面」按钮");
    // 剧集组 tab 已从非剧集组页面移除
    assert.ok(!Array.from(shadow.querySelectorAll('[data-action="view"]')).some((btn) => btn.dataset.view === "groups"), "编辑页不应出现剧集组页签");
    // Esc 关闭（脚本监听在 document 上）
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flush();
    assert.ok(!overlay.classList.contains("open"), "Esc 应关闭浮层");
    // 输入法组字期按键不算快捷键：面板输入框打中文时按 Esc 取消候选词不应关浮层（v1.0.1 回归）
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(overlay.classList.contains("open"), "重新点击悬浮球后浮层应打开");
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", isComposing: true, bubbles: true }));
    await flush();
    assert.ok(overlay.classList.contains("open"), "组字期 Esc（isComposing=true）不应关闭浮层");
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", keyCode: 229, bubbles: true }));
    await flush();
    assert.ok(overlay.classList.contains("open"), "组字期 Esc（keyCode=229）不应关闭浮层");
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flush();
    assert.ok(!overlay.classList.contains("open"), "组字结束后普通 Esc 仍应关闭浮层");
}

// —— 搜索 + 复制：粘贴文本来源 → 解析 → 条目头 + 复制按钮 → 剪贴板内容正确 ——
{
    const { clipboardWrites, shadow } = await boot("/movie/550-fight-club/edit", { withApiKey: false });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    // 切到「粘贴文本」来源并解析
    shadow.querySelector('[data-action="source-tab"][data-source-tab="text"]').click();
    await flush();
    const textarea = shadow.querySelector('[data-role="entry-text"]');
    textarea.value = [
        "标题：搏击俱乐部",
        "原名：Fight Club",
        "年份：1999",
        "首播：1999-10-15",
        "片长：139",
        "类型：剧情",
        "制片国家/地区：美国",
        "简介：一场关于自我对抗的黑色经典，足够长的简介文本"
    ].join("\n");
    shadow.querySelector('[data-action="parse-entry"]').click();
    await flush();
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header, "解析后右栏应出现来源条目头");
    assert.ok(header.textContent.includes("搏击俱乐部"), "条目头应展示来源标题");
    // 复制按钮组
    const copyRow = shadow.querySelector('[data-role="copy-row"]');
    assert.ok(copyRow, "条目头应带复制按钮组");
    copyRow.querySelector('[data-copy="titleYear"]').click();
    await flush();
    assert.ok(clipboardWrites.includes("搏击俱乐部 (1999)"), `「名称 (年份)」复制内容不符：${JSON.stringify(clipboardWrites)}`);
    copyRow.querySelector('[data-copy="title"]').click();
    await flush();
    assert.ok(clipboardWrites.includes("搏击俱乐部"), "应能复制名称");
    copyRow.querySelector('[data-copy="originalTitle"]').click();
    await flush();
    assert.ok(clipboardWrites.includes("Fight Club"), "应能复制原名");
    copyRow.querySelector('[data-copy="date"]').click();
    await flush();
    assert.ok(clipboardWrites.includes("1999-10-15"), "应能复制日期");
    copyRow.querySelector('[data-copy="overview"]').click();
    await flush();
    assert.ok(clipboardWrites.some((t) => t.includes("自我对抗")), "应能复制简介");
    copyRow.querySelector('[data-action="copy-record"]').click();
    await flush();
    const fullText = clipboardWrites[clipboardWrites.length - 1];
    assert.ok(fullText.includes("年份：1999") && fullText.includes("类型：剧情") && fullText.includes("简介："), "「复制全部信息」应包含关键字段");
    // 清除来源
    shadow.querySelector('[data-action="record-clear"]').click();
    await flush();
    assert.ok(!shadow.querySelector('[data-role="copy-row"]'), "清除后复制按钮组应消失");
}

// —— 候选卡复制：⧉ 按钮直接复制「名称 (年份)」，不触发载入 ——
{
    const routes = [
        {
            match: (url) => url.includes("/j/subject_suggest"),
            body: [{ id: "38626252", title: "香辣江湖擂台", sub_title: "Spicy Arena", year: "2026", type: "tv", url: "https://movie.douban.com/subject/38626252/" }]
        }
    ];
    const { clipboardWrites, shadow } = await boot("/tv/1399/edit", {
        seed: { "Tmdb.Helper.Config": JSON.stringify({ tmdb: { apiKey: "test-key-1234567890" }, douban: { enabled: true, minIntervalMs: 500 } }) },
        routes
    });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "香辣江湖擂台";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(700);
    const copyBtn = shadow.querySelector('[data-action="candidate-copy"]');
    assert.ok(copyBtn, "候选卡上应有 ⧉ 复制按钮");
    copyBtn.click();
    await flush();
    assert.ok(clipboardWrites.includes("香辣江湖擂台 (2026)"), "候选卡 ⧉ 应复制「名称 (年份)」");
    // 点 ⧉ 不应把候选详情载入成来源条目（与点卡片本身区分）
    assert.ok(!shadow.querySelector(".tmdbh-media-copy"), "点 ⧉ 不应触发候选载入");
}

// —— 豆瓣来源：suggest → rexxar 详情（含导演/主演）→ 候选卡 → 载入 ——
{
    const routes = [
        {
            match: (url) => url.includes("/j/subject_suggest"),
            body: [{ id: "38626252", title: "香辣江湖擂台", sub_title: "Spicy Arena", year: "2026", type: "tv", url: "https://movie.douban.com/subject/38626252/" }]
        },
        {
            match: (url) => url.includes("rexxar/api/v2/movie/38626252"),
            body: {
                title: "香辣江湖擂台",
                original_title: "",
                aka: ["Spicy Arena"],
                year: "2026",
                pubdate: ["2026-08-15(中国大陆)"],
                genres: ["纪录片"],
                countries: ["中国大陆"],
                durations: ["30分钟"],
                episodes_count: "6",
                intro: "一档足够长的简介内容用于冒烟测试",
                rating: { value: 8.6 },
                directors: [{ name: "导演甲" }],
                actors: ["主演甲", { name: "主演乙" }],
                pic: { large: "https://img1.doubanio.com/l.jpg" }
            }
        }
    ];
    const { shadow } = await boot("/tv/1399/edit", {
        seed: { "Tmdb.Helper.Config": JSON.stringify({ tmdb: { apiKey: "test-key-1234567890" }, douban: { enabled: true, minIntervalMs: 500 } }) },
        routes
    });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "香辣江湖擂台";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(700);
    const candCards = Array.from(shadow.querySelectorAll(".tmdbh-candidate"));
    assert.equal(candCards.length, 1, "豆瓣 suggest 应产出 1 张候选卡");
    candCards[0].click();
    await flush(800);
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header && header.textContent.includes("香辣江湖擂台"), "候选卡载入后应展示豆瓣详情");
    assert.ok(header.textContent.includes("导演甲"), "详情应展示导演");
    assert.ok(header.textContent.includes("主演乙"), "详情应展示主演");
    assert.ok(shadow.querySelector('[data-role="copy-row"]'), "豆瓣详情载入后应出现复制按钮组");
}

// —— IMDb 来源：TMDB find 反查 + 详情（fixture） ——
{
    const routes = [
        { match: (url) => /\/3\/find\/tt0133093/i.test(url), body: { movie_results: [{ id: 550, title: "Fight Club" }], tv_results: [] } },
        { match: (url) => url.includes("/3/movie/550"), body: { id: 550, title: "搏击俱乐部", original_title: "Fight Club", release_date: "1999-10-15", runtime: 139, overview: "概述文本", vote_average: 8.4, credits: { cast: [], crew: [] } } }
    ];
    const { shadow } = await boot("/movie/550-fight-club/edit", { routes });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    shadow.querySelector('[data-action="source-tab"][data-source-tab="imdb"]').click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "tt0133093";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(80);
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header && header.textContent.includes("搏击俱乐部"), "IMDb 反查应载入 TMDB 详情");
}

// —— 详情页：只搜索 + 右键悬浮球复制「名称 (年份) {tmdbid=…}」；不再自动携带旧来源 ——
{
    const routes = [
        {
            match: (url) => url.includes("/3/tv/1399"),
            body: { id: 1399, name: "权力的游戏", original_name: "Game of Thrones", first_air_date: "2011-04-17", number_of_episodes: 73, overview: "TMDB 官方简介内容", genres: [{ name: "剧情" }], production_countries: [{ name: "美国" }], vote_average: 9.2, credits: { cast: [], crew: [] } }
        }
    ];
    const doubanLast = { doubanId: "1", title: "权力的游戏", year: 2011, date: "2011-04-17", overview: "豆瓣来源的简介文本，足够长", genres: "剧情/奇幻", countries: "美国", rating: "9.0", doubanUrl: "https://movie.douban.com/subject/1/" };
    const { window, clipboardWrites, shadow } = await boot("/tv/1399-game-of-thrones", {
        title: "权力的游戏 (TV Series 2011) — The Movie Database (TMDB)",
        routes,
        seed: {
            "Tmdb.Helper.Config": JSON.stringify({ tmdb: { apiKey: "test-key-1234567890" }, douban: { enabled: true, minIntervalMs: 500 } }),
            "Tmdb.Helper.DoubanLast": JSON.stringify(doubanLast)
        }
    });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush(120);
    // 旧版跨页自动携带已移除：打开浮层后不应自动载入上次来源
    assert.ok(!shadow.querySelector(".tmdbh-media-copy"), "不应自动恢复上次的豆瓣来源");
    // 右键悬浮球：直接复制 tmdbid 标记（不依赖浮层内容）
    const ball = shadow.querySelector(".tmdbh-ball");
    ball.dispatchEvent(new window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    await flush();
    const marker = clipboardWrites[clipboardWrites.length - 1];
    assert.equal(marker, "权力的游戏 (2011) {tmdbid=1399}", `右键复制内容不符：${marker}`);
}

// —— 搜索框支持 {tmdbid=} 标记：直接按 TMDB ID 载入 ——
{
    const routes = [
        { match: (url) => url.includes("/3/movie/550"), body: { id: 550, title: "搏击俱乐部", original_title: "Fight Club", release_date: "1999-10-15", runtime: 139, overview: "概述文本", vote_average: 8.4, credits: { cast: [], crew: [] } } }
    ];
    const { shadow } = await boot("/movie/550-fight-club/edit", { routes });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "搏击俱乐部 {tmdbid=550}";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(200);
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header && header.textContent.includes("搏击俱乐部"), "tmdbid 标记应直接载入 TMDB 条目");
}

// —— 季编辑器（集编辑器重写）：灵活排期（周一/四 各两集）+ 粘贴解析含缩略图 URL ——
{
    const seasonRoutes = [
        { match: (url) => url.includes("/remote/episodes"), body: [{ id: 11, episode_number: 1, name: "旧集", air_date: "2025-12-01", overview: "", runtime: 0, bson_id: "aa11bb22cc33" }] }
    ];
    const { window, shadow } = await boot("/tv/1399-game-of-thrones/season/1/edit", {
        routes: seasonRoutes
    });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush(60);
    // 默认视图 = episodes；已有集应被预读（建议下一集从 2 开始）
    assert.ok(shadow.querySelector('[data-gen="pattern"]'), "集编辑器应有排期生成器");
    // 填排期：每周一、周四各更两集，从 2026-01-05（周一）起 6 集
    shadow.querySelector('[data-gen="pattern"]').value = "weekly";
    shadow.querySelector('[data-gen="startNumber"]').value = "1";
    shadow.querySelector('[data-gen="count"]').value = "6";
    shadow.querySelector('[data-gen="perSlot"]').value = "2";
    shadow.querySelector('[data-gen="startDate"]').value = "2026-01-05";
    shadow.querySelector('[data-gen="weekdays"]').value = "一,四";
    shadow.querySelector('[data-gen="titleTemplate"]').value = "第{n}集";
    shadow.querySelector('[data-action="ep-generate"]').click();
    await flush(80);
    const dateInputs = Array.from(shadow.querySelectorAll("[data-ep-date]"));
    assert.equal(dateInputs.length, 6, "排期应生成 6 行分集");
    assert.deepEqual(
        dateInputs.map((input) => input.value),
        ["2026-01-05", "2026-01-05", "2026-01-08", "2026-01-08", "2026-01-12", "2026-01-12"],
        "周一/周四各两集、同日共用日期"
    );
    assert.ok(!shadow.querySelector('[data-ep-check="1"]').checked, "已有集（fixture E1）默认不勾选");
    assert.ok(shadow.querySelector('[data-ep-check="1"]').closest("tr").textContent.includes("已存在"), "已有集应标记「已存在」");
    assert.ok(shadow.querySelector('[data-ep-check="3"]').checked, "新生成的集默认勾选");
    // 粘贴解析：TSV 带缩略图 URL → still 列渲染
    shadow.querySelector('[data-role="episode-text"]').value = "3\t第三集\t2026-01-15\t45\t简介\thttps://img.example/s3.jpg";
    shadow.querySelector('[data-action="parse-episodes"]').click();
    await flush(80);
    const stillInput = shadow.querySelector('[data-ep-still="3"]');
    assert.ok(stillInput, "分集表格应有缩略图 URL 列");
    assert.equal(stillInput.value, "https://img.example/s3.jpg", "缩略图 URL 应解析进表格");
    // 批量操作注册表：内置动作出现
    assert.ok(shadow.querySelector('[data-ep-action="builtin-stills-pipeline"]'), "内置缩略图批量动作应出现在批量操作区");
    // 季表单写入已移除
    assert.ok(!shadow.querySelector('[data-action="season-write"]'), "不应再有「写入季表单」按钮");
    // Esc 关闭浮层
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flush();
    assert.ok(!shadow.querySelector('[data-role="overlay"]').classList.contains("open"), "Esc 应关闭浮层");
}

// —— 平台链接输入框：粘贴后经历重渲染（setLoading）不得丢内容（回归：输入框变成"h"） ——
{
    const { window, shadow } = await boot("/tv/1399-game-of-thrones/season/1/edit", {
        routes: [{ match: (url) => url.includes("/remote/episodes"), body: [] }]
    });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    const pasted = "https://www.iqiyi.com/v_mcm2jefr68.html?vfrm=pcw_album_auto&rfr=https://www.google.com/";
    const siteUrl = shadow.querySelector('[data-role="site-url"]');
    assert.ok(siteUrl, "集编辑器应有平台链接输入框");
    siteUrl.value = pasted;
    siteUrl.dispatchEvent(new window.Event("input", { bubbles: true }));
    await flush();
    // 点「抓平台分集」：抓取必失败（沙箱断网），期间两次 setLoading 重渲染
    shadow.querySelector('[data-action="site-fetch"]').click();
    await flush(80);
    const after = shadow.querySelector('[data-role="site-url"]');
    assert.ok(after, "重渲染后输入框应仍存在");
    assert.equal(after.value, pasted, "重渲染后粘贴的链接不应丢失");
}

// —— 扩展接口：registerDataSource / registerEpisodeAction 即插即用 ——
{
    const { window, shadow } = await boot("/movie/550-fight-club/edit");
    await flush();
    const bridge = window.__tmdbHelper;
    assert.ok(bridge, "应暴露 window.__tmdbHelper 扩展接口");
    bridge.registerDataSource({
        id: "bangumi",
        name: "Bangumi",
        hint: "示例数据源",
        async search(query) {
            return [{ source: "bangumi", sourceId: "1", title: `${query}（番）`, overview: "示例简介", year: 2024 }];
        }
    });
    bridge.registerEpisodeAction({
        id: "my-op",
        title: "我的批量操作",
        when: () => true,
        run: async () => "扩展动作执行成功"
    });
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    const bangumiTab = shadow.querySelector('[data-source-tab="bangumi"]');
    assert.ok(bangumiTab, "注册的自定义数据源应出现在来源页签");
    bangumiTab.click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "测试";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(60);
    const cand = shadow.querySelector(".tmdbh-candidate");
    assert.ok(cand, "自定义数据源应产出候选卡");
    cand.click();
    await flush(60);
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header && header.textContent.includes("测试（番）"), "自定义数据源候选应可直接载入");
    // 集编辑器批量操作按钮（搜索页看不到，切到有集编辑器的页面验证）——用另一 boot 验证
    const seasonBoot = await boot("/tv/1399/season/1/edit", { routes: [{ match: (url) => url.includes("/remote/episodes"), body: [] }] });
    await flush();
    const bridge2 = seasonBoot.window.__tmdbHelper;
    bridge2.registerEpisodeAction({ id: "my-op", title: "我的批量操作", when: () => true, run: async () => "ok" });
    seasonBoot.shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(seasonBoot.shadow.querySelector('[data-ep-action="my-op"]'), "注册的批量操作应出现在集编辑器");
}

// —— 图片上传页（含单集剧照页）：上传配置解析 + 直链抓取/直传入口 ——
{
    const kendo = `
        <div class="results" data-media-id="aabbccddeeff0011"><ul class="images"></ul></div>
        <script type="text/javascript">
            $("#upload_files").kendoUpload({ upload: function(e) { e.data = { media_id: 'aabbccddeeff0011', media_type: 'TvEpisode', type: 'still', translate: false }; } });
        </script>
    `;
    const { shadow } = await boot("/tv/1399/season/1/episode/1/images/backdrops", { html: kendo, withApiKey: false });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    const directBtn = shadow.querySelector('[data-action="upload-direct"]');
    assert.ok(directBtn, "单集剧照页应有直传按钮");
    assert.ok(directBtn.disabled, "未抓取图片前直传按钮应禁用");
    assert.ok(shadow.querySelector('[data-role="poster-url"]'), "上传视图应有图片直链输入框");
    assert.ok(shadow.querySelector('[data-action="poster-load"]'), "上传视图应有「抓取直链图片」按钮");
    assert.ok(shadow.querySelector('[data-role="poster-crop"]'), "上传视图应有封面裁剪选择（左半/右半/居中）");
    assert.ok(shadow.querySelector('[data-role="body"]').textContent.includes("TvEpisode") || shadow.querySelector('[data-role="body"]').textContent.includes("单集"), "上传视图应展示解析到的目标类型");
    // 跨页海报携带已移除
    assert.ok(!shadow.querySelector('[data-action="carry-load"]'), "不应再有「恢复携带的图片」按钮");
}

// —— 季海报页：上传视图可用（季海报也支持直传） ——
{
    const kendo = `
        <div class="results" data-media-id="bbccddeeff001122"><ul class="images"></ul></div>
        <script type="text/javascript">
            $("#upload_files").kendoUpload({ upload: function(e) { e.data = { media_id: 'bbccddeeff001122', media_type: 'TvSeason', type: 'poster', translate: false }; } });
        </script>
    `;
    const { shadow } = await boot("/tv/1399/season/1/images/posters", { html: kendo, withApiKey: false });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(shadow.querySelector('[data-action="upload-direct"]'), "季海报页应有直传按钮");
    assert.ok(shadow.querySelector('[data-role="body"]').textContent.includes("季"), "季海报页应显示目标类型为季");
}

// —— 新增页：只有搜索，没有任何自动填充/待写入卡片 ——
{
    const pending = {
        mediaType: "movie",
        entry: { title: "测试电影", originalTitle: "Test Movie", date: "2024-01-01", runtime: 0, overview: "简介内容", genres: "剧情", countries: "中国大陆" },
        entryYear: 2024,
        posterUrl: "https://img1.doubanio.com/p.jpg",
        doubanUrl: "https://movie.douban.com/subject/1/",
        createdAt: Date.now()
    };
    const { shadow } = await boot("/movie/new", { seed: { "Tmdb.Helper.PendingNew": JSON.stringify(pending) } });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    const body = shadow.querySelector('[data-role="body"]');
    assert.ok(body.querySelector('[data-role="source-query"]'), "新增页浮层应是搜索视图");
    assert.ok(!body.querySelector('[data-action="pending-write"]'), "新增页不应再有「写入新增表单」按钮");
    assert.ok(!body.textContent.includes("待新增条目"), "新增页不应渲染待写入卡片（旧草稿被忽略）");
}

// —— 快捷键：Alt+T 隐藏/显示悬浮球 ——
{
    const { window, host } = await boot("/movie/550-fight-club/edit");
    await flush();
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "t", altKey: true, bubbles: true }));
    await flush();
    assert.equal(host.style.display, "none", "Alt+T 应隐藏悬浮球");
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "t", altKey: true, bubbles: true }));
    await flush();
    assert.equal(host.style.display, "", "再按 Alt+T 应恢复悬浮球");
}

// —— v1.0.2 百度百科：页签 → 搜索 → 详情（导演/类型）→ 分集剧情抓取/复制 + 「上传海报」按钮 ——
{
    const baikeSearchHtml = `<!doctype html><html><body><div class="result-list">
<div><a href="/item/%E5%A4%A7%E7%89%8C%E9%A9%BE%E5%88%B0/2178476">大牌驾到</a><p>2014年播出的网络综艺节目。</p></div>
</div></body></html>`;
    const baikeDetailHtml = `<!doctype html><html><head><meta property="og:image" content="https://bkimg.cdn.bcebos.com/pic/p?x-bce-process=image/resize,h_240"></head><body>
<h1>大牌驾到</h1>
<div class="lemma-summary"><div class="para">《大牌驾到》是一档网络综艺脱口秀节目，简介文本足够长。</div></div>
<img class="main-img" src="https://bkimg.cdn.bcebos.com/pic/p?_x=200">
<div class="basic-info">
<dt class="basicInfo-item name">导 演</dt><dd class="basicInfo-item value">王为念 / 李三</dd>
<dt class="basicInfo-item name">类 型</dt><dd class="basicInfo-item value">脱口秀 / 综艺</dd>
<dt class="basicInfo-item name">首播时间</dt><dd class="basicInfo-item value">2014-03-06</dd>
</div>
<h2 class="para-title">分集剧情</h2>
<table><tr><th>集数</th><th>剧情简介</th></tr>
<tr><td>第1集</td><td>第一期节目的剧情内容，文本要足够长才能被认成剧情简介。</td></tr>
<tr><td>第2集</td><td>第二期剧情简介，同样是足够长的文本内容用于断言。</td></tr>
</table>
</body></html>`;
    const posterPageHtml = `<html><body><script>
$("#upload_files").kendoUpload({ upload: function(e) { e.data = { media_id: 'aabbccddeeff0011', media_type: 'Movie', type: 'poster', translate: false }; } });
</script></body></html>`;
    const routes = [
        { match: (url) => url.includes("baike.baidu.com/search"), body: baikeSearchHtml },
        { match: (url) => url.includes("baike.baidu.com/item"), body: baikeDetailHtml },
        { match: (url) => url.includes("/images/posters"), body: posterPageHtml }
    ];
    const { clipboardWrites, shadow } = await boot("/movie/550-fight-club/edit", { routes, withApiKey: false });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    const baikeTab = shadow.querySelector('[data-source-tab="baike"]');
    assert.ok(baikeTab, "应出现「百度百科」来源页签");
    baikeTab.click();
    await flush();
    shadow.querySelector('[data-role="source-query"]').value = "大牌驾到";
    shadow.querySelector('[data-action="source-search"]').click();
    await flush(120);
    const cand = shadow.querySelector(".tmdbh-candidate");
    assert.ok(cand, "百科搜索应产出候选卡");
    cand.click();
    await flush(1400); // 百科节流 900ms：候选详情要等下一放行窗口
    const header = shadow.querySelector(".tmdbh-media-copy");
    assert.ok(header && header.textContent.includes("大牌驾到"), "百科候选应载入词条详情");
    assert.ok(header.textContent.includes("王为念"), "百科详情应展示导演");
    assert.ok(header.textContent.includes("脱口秀"), "百科详情应展示类型");
    assert.ok(shadow.querySelector('[data-action="baike-episodes"]'), "百科条目应出现「抓取分集剧情」按钮");
    assert.ok(shadow.querySelector('[data-action="record-poster-upload"]'), "有海报且页面可定位条目时应出现「上传海报」按钮");
    // 分集剧情：抓取 → 区块渲染 → 复制 TSV（同样要等节流放行）
    shadow.querySelector('[data-action="baike-episodes"]').click();
    await flush(1400);
    const epsBlock = shadow.querySelector('[data-role="baike-eps"]');
    assert.ok(epsBlock, "抓取后应出现分集剧情区块");
    assert.ok(epsBlock.textContent.includes("第一期"), "分集表格应包含抓到的剧情");
    assert.ok(epsBlock.querySelector('[data-action="baike-eps-copy"]'), "分集区块应有复制 TSV 按钮");
    assert.ok(!epsBlock.querySelector('[data-action="baike-eps-fill"]'), "非季编辑页不应出现「填入分集表格」按钮");
    epsBlock.querySelector('[data-action="baike-eps-copy"]').click();
    await flush();
    const tsv = clipboardWrites[clipboardWrites.length - 1];
    assert.ok(tsv.startsWith("1\t") && tsv.includes("第一期"), `复制 TSV 内容不符：${JSON.stringify(tsv)}`);
    // 上传海报：点击后走 目标解析（fetch 官方图片页）→ 下载（沙箱 GM 拿不到 blob → 优雅报错 toast）
    shadow.querySelector('[data-action="record-poster-upload"]').click();
    await flush(200);
    const toastEl = shadow.querySelector('[data-role="toast"]');
    assert.ok(toastEl.textContent.includes("上传海报失败"), `下载失败应优雅提示：${toastEl.textContent}`);
}

// —— v1.0.2 「上传海报」按钮回归：剧集/季详情页也要出现（tv-detail 上下文字段是 id 不是 tvId） ——
{
    const record = { source: "douban", sourceId: "25754848", title: "琅琊榜", poster: "https://img1.doubanio.com/view/photo/raw/public/p1.jpg" };
    const seedPanelState = JSON.stringify({ savedAt: Date.now(), kind: "tv-detail", tvId: null, record, candidates: [record] });
    const { shadow } = await boot("/tv/1399-game-of-thrones", { withApiKey: false, seed: { "Tmdb.Helper.PanelState": seedPanelState } });
    await flush();
    shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(shadow.querySelector(".tmdbh-media-copy"), "持久化的来源条目应恢复并渲染条目卡");
    assert.ok(shadow.querySelector('[data-action="record-poster-upload"]'), "剧集详情页 + 带海报条目应出现「上传海报」按钮");
    assert.ok(!shadow.querySelector('[data-action="baike-episodes"]'), "豆瓣条目不应出现百科分集按钮");
    // 季详情页：此前面板根本不注入，现在有搜索 + 上传海报（目标=该季海报库）
    // （持久化恢复按「同类型同 ID」匹配，seed 要用季详情自身的上下文）
    const seasonBoot = await boot("/tv/1399-game-of-thrones/season/2", {
        withApiKey: false,
        seed: { "Tmdb.Helper.PanelState": JSON.stringify({ savedAt: Date.now(), kind: "season-detail", tvId: 1399, record, candidates: [record] }) }
    });
    await flush();
    assert.ok(seasonBoot.shadow.querySelector(".tmdbh-ball"), "季详情页应注入悬浮球");
    seasonBoot.shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(seasonBoot.shadow.querySelector('[data-action="record-poster-upload"]'), "季详情页 + 带海报条目应出现「上传海报」按钮");
    // 新增页没有条目 ID，不应出现上传海报按钮
    const newBoot = await boot("/movie/new", { withApiKey: false, seed: { "Tmdb.Helper.PanelState": seedPanelState } });
    await flush();
    newBoot.shadow.querySelector(".tmdbh-ball").click();
    await flush();
    assert.ok(!newBoot.shadow.querySelector('[data-action="record-poster-upload"]'), "新增页（无条目 ID）不应出现「上传海报」按钮");
}

console.log("tmdb-ui-smoke：浮层引导、搜索复制（豆瓣/IMDb/粘贴文本/百度百科）、集编辑器、图片上传、扩展接口全部通过 ✓");
