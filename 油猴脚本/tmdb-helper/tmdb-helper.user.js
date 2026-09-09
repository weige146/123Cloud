// ==UserScript==
// @name         TMDB 助手
// @namespace    local.tmdb-helper
// @version      1.0.2
// @description  在 themoviedb.org 提供轻量搜索浮层：以统一数据源接口（豆瓣 / 百度百科 / IMDb / 粘贴文本，可插拔新数据源）搜索条目，结果一键复制（名称 / 原名 / 名称 (年份) / 简介 / 全部信息），并支持一键把来源海报按 TMDB 官方规范直传到当前条目；百度百科条目还能一键抓取词条里的分集剧情（复制 TSV 或填入季编辑器分集表格）。豆瓣详情字段补全：rexxar 接口缺的编剧/语言/又名等自动用桌面页合并。悬浮球左键开关浮层、右键直接复制「名称 (年份) {tmdbid=…}」。保留的页面功能只在对应页面出现：季编辑器的批量单集（灵活排期 + 过滤词剔除重编号 + 平台链接抓分集（B站/爱奇艺/腾讯/芒果/优酷/红果短剧）+ 从 TMDB 拉取官方分集回填 + 分集缩略图按 TMDB 官方规范抓取/去黑边/裁切/直传）、图片上传页的直传（电影/剧集/季海报、背景图、标志、单集剧照，直链图自动去黑边）、剧集组页的剧集组管理（官网内部接口），并预留批量操作扩展接口（window.TmdbHelper）。右侧停靠面板：鸿蒙光感玻璃风 UI（磨砂透光、蓝紫渐变，跟随系统深浅主题，面板宽度可拖拽）。
// @license      MIT
// @icon         https://www.themoviedb.org/favicon.ico
// @match        *://*.themoviedb.org/*
// @match        *://themoviedb.org/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_info
// @grant        unsafeWindow
// @connect      themoviedb.org
// @connect      *.themoviedb.org
// @connect      douban.com
// @connect      *.douban.com
// @connect      doubanio.com
// @connect      *.doubanio.com
// @connect      baike.baidu.com
// @connect      bkimg.cdn.bcebos.com
// @connect      *
// @noframes
// @homepageURL  https://greasyfork.org/zh-CN/scripts/594907-tmdb-%E5%8A%A9%E6%89%8B
// @supportURL   https://github.com/weige146/123Cloud/issues
// @updateURL    https://update.greasyfork.org/scripts/594907/TMDB%20%E5%8A%A9%E6%89%8B.meta.js
// @downloadURL  https://update.greasyfork.org/scripts/594907/TMDB%20%E5%8A%A9%E6%89%8B.user.js
// ==/UserScript==

    /*
     * 使用说明（维护者速览）——v1.0.0：
     * - 悬浮窗只有一个「搜索」视图：统一数据源接口（豆瓣 / IMDb(经 TMDB 反查) / 粘贴文本，可插拔新数据源）
     *   搜索 → 候选卡 → 载入统一条目记录；一切围绕「方便复制」：
     *   候选卡与来源条目上都有复制按钮（名称 / 原名 / 名称 (年份) / 日期 / 简介 / 全部信息）。
 * - 悬浮球：左键开关浮层（只有点击才显示，刷新/跳转后绝不自动弹出）；右键直接复制
 *   「名称 (年份) {tmdbid=…}」（123 助手同款格式）；可拖拽；Alt+T 隐藏/恢复。
 *   v1.0.1：全局快捷键忽略输入法组字期按键（isComposing/keyCode 229）——面板输入框里打中文、
 *   按 Esc 取消候选词不再误关整个浮层（与 123 助手同款防护）。
 *   v1.0.2：搜索浮层三大增强——
 *   1) 新增「百度百科」数据源：关键词搜索（/search?word= 解析，改版时兜底词条名直连）、
 *      条目链接直载；详情解析兼容多代页面结构（basicInfo-item/lemma-summary + og:meta 兜底），
 *      抓 导演/编剧/主演/类型/制片地区/语言/集数/首播/每集长度/出品方/播出平台/海报（bkimg 升原图）；
 *      条目卡一键「抓取分集剧情」（分集剧情表格 → 集数/集名/简介，可复制 TSV、季编辑页可一键填表）；
 *      百度风控（安全验证页）给出明确提示。bkimg 图片水合与防盗链 Referer 同步支持。
 *   2) 搜索浮层「⬆ 上传海报」：来源条目带海报且当前页面可定位 TMDB 条目（电影/剧集详情页、编辑页、
 *      季详情页、季编辑页、图片上传页）时出现——按页面上下文同源抓官方图片页拿上传配置 → 下载海报 →
 *      按 TMDB 规范（2:3 居中裁剪、等比缩小、禁放大）处理 → POST /image 直传；小图变体
 *      （豆瓣 m_ratio / 百科缩放参数 / TMDB w 后缀）自动升级原图。
 *      注意：剧集详情/编辑页的上下文字段是 id（tvId 只在 season/episode 系页面才有）——
 *      初版误读 tvId 导致剧集页按钮不渲染，已修；季详情页（/tv/{id}/season/{n}）此前是
 *      未适配页（面板不注入），现在提供搜索 + 上传海报（目标=该季海报库）。
 *   3) 豆瓣字段补全：rexxar 接口编剧恒缺、语言/又名常缺——解析层补采 languages/aka，
 *      详情抓取后若仍有空字段（编剧/导演/类型/地区/语言/又名/简介）自动抓桌面页合并（只填空位
 *      不覆盖，匿名请求拿到 JS 壳时静默跳过）；「复制全部信息」与条目卡展示增加语言行。
 * - 不再向官方页面回写任何数据：对照填写/字段绑定/新增向导自动填充/季表单写入已全部移除，
 *   脚本只读页面 + 只提供搜索结果信息。
     * - 保留的页面功能（只在对应页面出现，浮层依然是点击才开）：
     *   1) 季编辑器（/tv/{id}/season/{n}/edit）→「批量单集」：灵活排期（每周固定日、单日多集、固定间隔）、
     *      过滤词剔除 + 重编号（TMDB-Import 同款：PV/预告/花絮行剔除，保留原有缺集）、
     *      平台链接抓分集（B站 bangumi / 爱奇艺 / 腾讯视频 / 芒果TV / 优酷，公开 JSON 接口移植自
     *      TMDB-Import 的免浏览器提取器，src/sites.js）、红果短剧（hongguoduanju.com：输入剧名站内搜索
     *      → 候选点选 → SSR 页 _ROUTER_DATA 里的 vid_list 即整季集号；附剧名/简介/封面信息卡）、
     *      从 TMDB 官方 API 拉取整季分集回填表格（tmdb-scraper 同款，按集号合并只补空字段）、
     *      分集表格、走官网内部接口的批量提交，分集缩略图（剧照）按 TMDB 官方图片规范
     *      （16:9、1280×720 起、JPG、禁止放大小图）抓取→去黑边→居中裁切→直传；
     *      季海报/背景图走「季海报上传页」按钮到官方季图片页直传；
     *   2) 图片上传页（电影/剧集/季海报、背景图、标志、单集剧照页）→「上传图片」：解析页面内嵌上传配置，
     *      支持图片直链抓取后直传（16:9 类自动去黑边）、本地图片按官方规范校验转换后直传（含季海报页）；
 *   3) 剧集组页（/tv/{id}/episode_group/{gid} 及其编辑页）→「剧集组」：读取结构、增删改组与子组、
 *      勾选单集加入子组、按季集重排（官网内部接口）。其他页面（编辑/详情/新增）只有搜索。
 * - 批量操作扩展接口：window.TmdbHelper.registerEpisodeAction({id,title,when,run}) 注册批量动作，
 *   会出现在集编辑器「批量操作」区；registerDataSource 注册新数据源。
 * - 深色模式、Esc 关闭浮层、Ctrl+Enter 快捷搜索/解析。
 */

(() => {
    "use strict";

    // src/config.js —— 默认配置、归一化与存储（纯逻辑，供测试切片）
    const TMDBH_CONFIG_KEY = "Tmdb.Helper.Config";

    const TMDBH_DEFAULT_CONFIG = Object.freeze({
        theme: "auto",
        douban: { enabled: true, minIntervalMs: 2000 },
        tmdb: { apiKey: "" },
        filters: { words: "PV,预告,花絮,特典,NCOP,NCED,CM,菜单,Menu,Special" },
        episodes: { delayMs: 1000, retries: 1 },
        stills: { delayMs: 1500 },
        panel: { x: null, y: null }
    });

    function tmdbhClampInt(value, min, max, fallback) {
        const num = Number(String(value ?? "").trim());
        if (!Number.isFinite(num)) return fallback;
        return Math.min(max, Math.max(min, Math.round(num)));
    }

    function tmdbhDeepMerge(base, patch) {
        if (Array.isArray(base)) return Array.isArray(patch) ? patch.slice() : base.slice();
        if (base && typeof base === "object") {
            const out = Object.assign({}, base);
            if (patch && typeof patch === "object") {
                for (const key of Object.keys(patch)) {
                    out[key] = tmdbhDeepMerge(base[key], patch[key]);
                }
            }
            return out;
        }
        return patch === undefined ? base : patch;
    }

    function tmdbhNumOrNull(value) {
        if (value === null || value === undefined || value === "") return null;
        const num = Number(value);
        return Number.isFinite(num) ? num : null;
    }

    function tmdbhNormalizeConfig(raw) {
        const merged = tmdbhDeepMerge(TMDBH_DEFAULT_CONFIG, raw && typeof raw === "object" ? raw : {});
        merged.theme = ["auto", "light", "dark"].includes(merged.theme) ? merged.theme : "auto";
        merged.douban.enabled = merged.douban.enabled !== false;
        merged.douban.minIntervalMs = tmdbhClampInt(merged.douban.minIntervalMs, 500, 10000, 2000);
        merged.tmdb.apiKey = String(merged.tmdb.apiKey || "").trim();
        merged.filters.words = String(merged.filters && merged.filters.words || "").trim();
        merged.episodes.delayMs = tmdbhClampInt(merged.episodes.delayMs, 0, 60000, 1000);
        merged.episodes.retries = tmdbhClampInt(merged.episodes.retries, 0, 3, 1);
        merged.stills.delayMs = tmdbhClampInt(merged.stills.delayMs, 0, 60000, 1500);
        merged.panel.x = tmdbhNumOrNull(merged.panel.x);
        merged.panel.y = tmdbhNumOrNull(merged.panel.y);
        return merged;
    }

    class TmdbhConfigStore {
        constructor(storage, key = TMDBH_CONFIG_KEY) {
            this.storage = storage;
            this.key = key;
            this.cache = null;
        }

        load() {
            if (!this.cache) {
                let stored = null;
                try {
                    stored = JSON.parse(this.storage.get(this.key) || "null");
                } catch (err) {
                    stored = null;
                }
                this.cache = tmdbhNormalizeConfig(stored);
            }
            return this.cache;
        }

        get() {
            return JSON.parse(JSON.stringify(this.load()));
        }

        update(patch) {
            this.cache = tmdbhNormalizeConfig(tmdbhDeepMerge(this.load(), patch || {}));
            try {
                this.storage.set(this.key, JSON.stringify(this.cache));
            } catch (err) {
                /* 存储失败时仅在内存生效 */
            }
            return this.get();
        }
    }


    // src/parser.js —— 文本/分集解析、排期生成、页面上下文识别（纯逻辑，供测试切片）
    function tmdbhPad2(value) {
        return String(value).padStart(2, "0");
    }

    function parseYearValue(value) {
        const match = String(value || "").match(/(1[89]\d{2}|20\d{2})/);
        if (!match) return 0;
        const year = Number(match[1]);
        return year >= 1870 && year <= 2100 ? year : 0;
    }

    function normalizeAirDate(value) {
        const text = String(value || "").trim();
        if (!text) return "";
        let year = 0;
        let month = 0;
        let day = 0;
        let match = text.match(/(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/);
        if (match) {
            year = Number(match[1]);
            month = Number(match[2]);
            day = Number(match[3]);
        } else {
            match = text.match(/^(\d{4})(\d{2})(\d{2})$/);
            if (!match) return "";
            year = Number(match[1]);
            month = Number(match[2]);
            day = Number(match[3]);
        }
        if (month < 1 || month > 12 || day < 1 || day > 31) return "";
        const date = new Date(Date.UTC(year, month - 1, day));
        if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return "";
        return `${year}-${tmdbhPad2(month)}-${tmdbhPad2(day)}`;
    }

    function cleanCell(value) {
        return String(value || "").replace(/^["'「『]+|["'」』]+$/g, "").trim();
    }

    function splitStructuredLine(line) {
        const text = String(line || "");
        if (!text.includes("\t") && !/[|｜]/.test(text)) return null;
        const cells = text.split(/\t|[|｜]/).map(cleanCell);
        if (cells.length < 2) return null;
        if (cells.every((cell) => !cell)) return null;
        return cells;
    }

    function collapseSummary(text) {
        return String(text || "")
            .split(/\r?\n/)
            .map((line) => line.replace(/\s+/g, " ").trim())
            .filter(Boolean)
            .join("\n")
            .trim();
    }

    function looksLatin(value) {
        const text = String(value || "");
        return text.length >= 2 && /[A-Za-z]/.test(text) && !/[\u4e00-\u9fff]/.test(text);
    }

    function normalizeForMatch(value) {
        return String(value || "").replace(/[\p{P}\p{S}\s_]+/gu, "").toLowerCase();
    }

    const ENTRY_LABELS = [
        { key: "originalTitle", labels: ["原始标题", "原始名称", "外文原名", "外文名", "英文片名", "英文剧名", "原名", "原题", "original title", "original name"] },
        { key: "title", labels: ["中文片名", "中文剧名", "标题", "片名", "剧名", "名称", "译名", "title", "name"] },
        { key: "year", labels: ["年份", "年代", "year"] },
        { key: "date", labels: ["上映日期", "首播日期", "播出日期", "上映", "首播", "播出", "release date", "first air date", "air date", "premiere"] },
        { key: "overview", labels: ["剧情简介", "内容简介", "简介", "剧情", "梗概", "概要", "overview", "synopsis", "storyline", "plot", "story"] },
        { key: "runtime", labels: ["单集片长", "运行时间", "片长", "时长", "runtime", "duration"] },
        { key: "genres", labels: ["影片类型", "节目类型", "类型", "genre", "genres"] },
        { key: "countries", labels: ["制片国家/地区", "国家/地区", "制片地区", "country", "region"] },
        { key: "aliases", labels: ["又名", "别名", "aka", "also known as"] },
        { key: "tagline", labels: ["宣传语", "标语", "tagline"] },
        { key: "homepage", labels: ["官方网站", "官方主页", "主页", "homepage", "website"] },
        { key: "imdb", labels: ["imdb 编号", "imdb编号", "imdb id", "imdb"] },
        { key: "productionCode", labels: ["制作代码", "制片编码", "production code"] },
        { key: "directors", labels: ["导演", "directors", "director"] },
        { key: "cast", labels: ["主演", "主演阵容", "演员", "cast", "starring"] }
    ];

    function matchEntryLabel(line) {
        const lower = String(line || "").toLowerCase();
        const candidates = [];
        for (const group of ENTRY_LABELS) {
            for (const label of group.labels) {
                const lowerLabel = label.toLowerCase();
                if (lower.startsWith(lowerLabel)) {
                    candidates.push({ label: lowerLabel, key: group.key, cjk: /[\u4e00-\u9fff]/.test(label) });
                }
            }
        }
        if (!candidates.length) return null;
        candidates.sort((a, b) => b.label.length - a.label.length);
        for (const cand of candidates) {
            const rest = String(line).slice(cand.label.length);
            if (/^\s*[::：]/.test(rest)) {
                return { key: cand.key, value: rest.replace(/^\s*[::：]\s*/, "").trim() };
            }
            if (rest.trim() === "") {
                return { key: cand.key, value: "" };
            }
            // 英文单词容易误吃普通句子，只有中文标签允许「标签 值」的空格写法
            if (cand.cjk && /^\s+\S/.test(rest)) {
                return { key: cand.key, value: rest.trim() };
            }
        }
        return null;
    }

    function applyStructuredEntryRow(result, cells) {
        const filled = cells.filter((cell) => cell !== "");
        if (!filled.length) return;
        if (!result.title) result.title = filled[0];
        for (let i = 1; i < filled.length; i++) {
            const cell = filled[i];
            const date = normalizeAirDate(cell);
            if (!result.date && date) {
                result.date = date;
                continue;
            }
            const year = parseYearValue(cell);
            if (!result.year && year && cell.replace(/[^\d]/g, "").length <= 6 && cell.length <= 10) {
                result.year = year;
                continue;
            }
            const runtime = cell.match(/^(\d{1,4})\s*分?钟?$/);
            if (!result.runtime && runtime) {
                result.runtime = Number(runtime[1]);
                continue;
            }
            if (!result.originalTitle && looksLatin(cell)) {
                result.originalTitle = cell;
                continue;
            }
            if (!result.overview && cell.length >= 10) {
                result.overview = cell;
            }
        }
    }

    function applyStandaloneTitle(result, line) {
        if (!line) return;
        if (/^[\u4e00-\u9fffA-Za-z0-9]{1,10}\s*[::：]/.test(line.slice(0, 24))) return; // 疑似未收录标签行
        const match = line.match(/^(.*?)\s*[（(](\d{4})[)）]\s*$/);
        if (match) {
            result.title = match[1].trim();
            if (!result.year) result.year = Number(match[2]);
            return;
        }
        if (!result.title) result.title = line;
    }

    function splitAliases(value) {
        return String(value || "")
            .split(/[/、,，;；]/)
            .map((part) => part.trim())
            .filter(Boolean);
    }

    function parseEntryText(text) {
        const result = { title: "", originalTitle: "", year: 0, date: "", overview: "", runtime: 0, genres: "", countries: "", aliases: "", tagline: "", homepage: "", imdb: "", productionCode: "", directors: "", cast: "" };
        const lines = String(text || "")
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line && !/^[-=—_·*#]{3,}$/.test(line));
        const overviewLines = [];
        let currentKey = "";
        for (const line of lines) {
            const labeled = matchEntryLabel(line);
            if (labeled) {
                currentKey = labeled.key;
                const value = labeled.value;
                if (labeled.key === "overview") {
                    if (value) overviewLines.push(value);
                } else if (labeled.key === "title") {
                    if (value) applyStandaloneTitle(result, value);
                } else if (!value) {
                    /* 标签后留空的字段等后续独立行 */
                } else if (labeled.key === "originalTitle") {
                    if (!result.originalTitle) result.originalTitle = value;
                } else if (labeled.key === "year") {
                    if (!result.year) result.year = parseYearValue(value);
                } else if (labeled.key === "date") {
                    if (!result.date) {
                        const date = normalizeAirDate(value);
                        if (date) {
                            result.date = date;
                        } else {
                            const year = parseYearValue(value);
                            if (year && !result.year) result.year = year;
                        }
                    }
                } else if (labeled.key === "runtime") {
                    if (!result.runtime) {
                        const match = value.match(/(\d{1,4})/);
                        if (match) result.runtime = Number(match[1]);
                    }
                } else if (labeled.key === "genres") {
                    if (!result.genres) result.genres = value;
                } else if (labeled.key === "countries") {
                    if (!result.countries) result.countries = value;
                } else if (labeled.key === "aliases") {
                    if (!result.aliases) result.aliases = value;
                } else if (labeled.key === "tagline") {
                    if (!result.tagline) result.tagline = value;
                } else if (labeled.key === "homepage") {
                    if (!result.homepage) result.homepage = value;
                } else if (labeled.key === "imdb") {
                    if (!result.imdb) {
                        const imdbMatch = value.match(/tt\d{5,}/i);
                        if (imdbMatch) result.imdb = imdbMatch[0];
                    }
                } else if (labeled.key === "productionCode") {
                    if (!result.productionCode) result.productionCode = value;
                } else if (labeled.key === "directors") {
                    if (!result.directors) result.directors = value;
                } else if (labeled.key === "cast") {
                    if (!result.cast) result.cast = value;
                }
                continue;
            }
            if (currentKey === "overview") {
                overviewLines.push(line);
                continue;
            }
            if (!result.title) {
                const cells = splitStructuredLine(line);
                if (cells && cells.length >= 2) {
                    applyStructuredEntryRow(result, cells);
                } else {
                    applyStandaloneTitle(result, line);
                }
                continue;
            }
            overviewLines.push(line);
        }
        if (overviewLines.length && !result.overview) result.overview = overviewLines.join("\n");
        result.overview = collapseSummary(result.overview);
        if (!result.originalTitle) {
            const latinAlias = splitAliases(result.aliases).find(looksLatin);
            if (latinAlias) result.originalTitle = latinAlias;
        }
        return result;
    }

    function extractEpisodeNumber(text) {
        const s = String(text || "").trim();
        let match = s.match(/^S\d{1,4}\s*E(\d{1,4})$/i);
        if (match) return Number(match[1]);
        match = s.match(/^(?:E|EP)\s*(\d{1,4})$/i);
        if (match) return Number(match[1]);
        match = s.match(/^第\s*(\d{1,4})\s*[集期话]$/);
        if (match) return Number(match[1]);
        match = s.match(/^#?(\d{1,4})$/);
        if (match) {
            const num = Number(match[1]);
            return num > 0 && num <= 2000 ? num : 0;
        }
        return 0;
    }

    function episodeFromCells(cells) {
        const number = extractEpisodeNumber(cells[0]);
        if (!number) return episodeFromFreeLine(cells.filter(Boolean).join(" "));
        const episode = { episodeNumber: number, name: "", airDate: "", overview: "", runtime: 0, stillUrl: "" };
        for (let i = 1; i < cells.length; i++) {
            const cell = cells[i];
            if (!cell) continue;
            const date = normalizeAirDate(cell);
            if (date && !episode.airDate) {
                episode.airDate = date;
                continue;
            }
            const runtime = cell.match(/^(\d{1,3})\s*分?钟?$/);
            if (!episode.runtime && runtime) {
                episode.runtime = Number(runtime[1]);
                continue;
            }
            if (/^https?:\/\//i.test(cell) && !episode.stillUrl) {
                episode.stillUrl = cell;
                continue;
            }
            if (!episode.name) {
                episode.name = cell;
                continue;
            }
            if (!episode.overview) episode.overview = cell;
        }
        return episode;
    }

    function episodeFromFreeLine(line) {
        let rest = String(line || "").trim();
        if (!rest) return null;
        let airDate = "";
        const dateMatch = rest.match(/(\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?|\d{8})/);
        if (dateMatch) {
            airDate = normalizeAirDate(dateMatch[1]);
            if (airDate) rest = rest.replace(dateMatch[1], " ");
        }
        let stillUrl = "";
        const urlMatch = rest.match(/https?:\/\/\S+/i);
        if (urlMatch) {
            stillUrl = urlMatch[0];
            rest = rest.replace(urlMatch[0], " ");
        }
        let number = 0;
        let match = rest.match(/^\s*(?:S\d{1,4}\s*E|E|EP|第|#)\s*(\d{1,4})\s*(?:[集期话])?\s*[::：.、\-—~]?\s*(.*)$/i);
        if (match) {
            number = Number(match[1]);
            rest = match[2] || "";
        } else {
            match = rest.match(/^\s*(\d{1,4})\s*[集期话]\s*[::：.、\-—~]?\s*(.*)$/);
            if (match) {
                number = Number(match[1]);
                rest = match[2] || "";
            } else {
                match = rest.match(/^\s*(\d{1,4})\s*[::：.、\-—~]\s*(.+)$/);
                if (match) {
                    number = Number(match[1]);
                    rest = match[2] || "";
                } else {
                    // 纯空格分隔只接受 1-3 位集数，避免把「1994 上映」这类年份行误判成分集
                    match = rest.match(/^\s*(\d{1,3})\s+(.+)$/);
                    if (match) {
                        number = Number(match[1]);
                        rest = match[2] || "";
                    }
                }
            }
        }
        if (!number || number > 2000) return null;
        rest = rest.replace(/^[\s::：.\-—、·]+/, "").replace(/[（(]\s*[)）]/g, "").trim();
        return { episodeNumber: number, name: rest, airDate, overview: "", runtime: 0, stillUrl };
    }

    function parseEpisodeLine(line) {
        const cells = splitStructuredLine(line);
        if (cells) return episodeFromCells(cells);
        // 分号分隔（兼容 TMDB-Import / tmdb-automation 的 episodes.txt：「1;2011/12/4;45;名字;简介」）
        if (/[;；]/.test(line)) {
            const semiCells = String(line).split(/[;；]/).map(cleanCell);
            if (semiCells.length >= 2 && extractEpisodeNumber(semiCells[0])) return episodeFromCells(semiCells);
        }
        return episodeFromFreeLine(line);
    }

    function parseEpisodeText(text) {
        const episodes = [];
        const lines = String(text || "").split(/\r?\n/);
        for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line || line.startsWith("#") || line.startsWith("//")) continue;
            if (/^[-=—_·*]{3,}$/.test(line)) continue;
            const parsed = parseEpisodeLine(line);
            if (parsed && parsed.episodeNumber) {
                episodes.push(parsed);
            } else if (episodes.length && line.length >= 2) {
                const last = episodes[episodes.length - 1];
                last.overview = last.overview ? `${last.overview}\n${line}` : line;
            }
        }
        episodes.sort((a, b) => a.episodeNumber - b.episodeNumber);
        return episodes;
    }

    function findDuplicateEpisodeNumbers(episodes) {
        const seen = new Set();
        const duplicates = new Set();
        for (const episode of episodes) {
            if (seen.has(episode.episodeNumber)) duplicates.add(episode.episodeNumber);
            else seen.add(episode.episodeNumber);
        }
        return Array.from(duplicates).sort((a, b) => a - b);
    }

    // 缺集检测：返回 [min, max] 范围内缺失的集号（用于提交前提示漏集）
    function findMissingEpisodeNumbers(episodes) {
        const numbers = (Array.isArray(episodes) ? episodes : [])
            .map((ep) => Number(ep.episodeNumber) || 0)
            .filter((n) => n > 0 && n <= 2000);
        if (!numbers.length) return [];
        const min = Math.min(...numbers);
        const max = Math.max(...numbers);
        if (max - min > 2000) return [];
        const present = new Set(numbers);
        const missing = [];
        for (let n = min; n <= max; n++) {
            if (!present.has(n)) missing.push(n);
        }
        return missing;
    }

    // 豆瓣海报 URL 升级为原图：移动端/缩略变体（m_ratio_poster 等）只有 ~480px 宽，
    // 过不了 TMDB 最低分辨率；豆瓣 CDN 支持 /view/photo/raw/public/ 取原图
    function upgradeDoubanPosterUrl(url) {
        const text = String(url || "").trim();
        if (!text) return text;
        if (!/doubanio\.com|douban\.com/i.test(text)) return text;
        return text.replace(/(\/view\/photo\/)(?!raw\/)[^/]+(\/public\/)/i, "$1raw$2");
    }

    // 百科图片 URL 升级为原图：bkimg CDN 的缩放参数（?x-bce-process=resize… / ?_x=…）把图压到几百像素，
    // 去掉 query 即原图，才能过 TMDB 最低分辨率
    function upgradeBaikePosterUrl(url) {
        const text = String(url || "").trim();
        if (!text) return text;
        if (!/bkimg\.cdn\.bcebos\.com|bkimg/i.test(text)) return text;
        return text.split("?")[0];
    }

    // —— TMDB-Import 式过滤词：命中标题的集剔除，剩余集重编号但保留原有缺集 ——
    // 例：[1,2,3(PV),5] 过滤掉 3 → [1,2,4]（PV 造成的跳档被补上，原本就缺的 4 保留跳档）。
    // words 支持 数组 或 逗号/顿号/空白分隔的字符串；匹配对标题做小写包含判断。
    function applyEpisodeFilterWords(episodes, words) {
        const list = (Array.isArray(episodes) ? episodes : []).slice();
        const wordList = (Array.isArray(words) ? words : String(words || "").split(/[,，、\s]+/))
            .map((word) => String(word || "").trim().toLowerCase())
            .filter(Boolean);
        if (!list.length || !wordList.length) return { episodes: list, removed: [] };
        const hit = (ep) => wordList.some((word) => String(ep.name || "").toLowerCase().includes(word));
        const removed = list.filter(hit);
        if (!removed.length) return { episodes: list, removed: [] };
        const kept = list.filter((ep) => !hit(ep));
        const originalNumbers = new Set(list.map((ep) => Number(ep.episodeNumber)));
        const min = Math.min(...originalNumbers);
        const gaps = [];
        for (let n = min; n <= Math.max(...originalNumbers); n++) {
            if (!originalNumbers.has(n)) gaps.push(n);
        }
        const episodesOut = kept.map((ep, index) => {
            const below = gaps.filter((g) => g < Number(ep.episodeNumber)).length;
            return Object.assign({}, ep, { episodeNumber: min + index + below });
        });
        return { episodes: episodesOut, removed };
    }

    // —— tmdb-scraper 式：TMDB 官方季详情（/tv/{id}/season/{n}）→ 分集表格行（与粘贴解析同构） ——
    function mapTmdbSeasonEpisodes(data) {
        const eps = data && typeof data === "object" && Array.isArray(data.episodes) ? data.episodes : [];
        return eps.map((ep) => ({
            episodeNumber: Number(ep.episode_number) || 0,
            name: String(ep.name || "").trim(),
            airDate: normalizeAirDate(ep.air_date || ""),
            runtime: Number(ep.runtime) || 0,
            overview: String(ep.overview || "").replace(/\s*\n\s*/g, " ").trim(),
            stillUrl: ep.still_path ? `https://image.tmdb.org/t/p/original${ep.still_path}` : ""
        })).filter((ep) => ep.episodeNumber > 0);
    }

    // 表格导出：TSV（集数/标题/日期/时长/简介/缩略图），可直接回贴到解析框
    function exportEpisodesToTsv(episodes) {
        return (Array.isArray(episodes) ? episodes : [])
            .map((ep) => [ep.episodeNumber, ep.name || "", ep.airDate || "", ep.runtime || "", String(ep.overview || "").replace(/\s*\n\s*/g, " "), ep.stillUrl || ""].join("\t"))
            .join("\n");
    }

    // 图片上传页内嵌的 kendo Upload 配置：media_id(bson)/media_type/type → 供面板直传 POST /image
    // media_type/type 只在 media_id 邻近的配置窗口内找，避免误抓页面上其它 ajax 的 type: 'POST'
    function parseImageUploadConfig(html) {
        const text = String(html || "");
        const mediaIdMatch = text.match(/media_id:\s*['"]([a-f0-9]{12,40})['"]/);
        if (!mediaIdMatch) return null;
        const scope = text.slice(mediaIdMatch.index, mediaIdMatch.index + 400);
        const mediaType = scope.match(/media_type:\s*['"]([^'"]+)['"]/);
        const type = scope.match(/(?:^|[^\w])type:\s*['"]([^'"]+)['"]/);
        return {
            mediaId: mediaIdMatch[1],
            mediaType: mediaType ? mediaType[1] : "",
            type: type && /^(poster|backdrop|logo|still)$/.test(type[1]) ? type[1] : "poster"
        };
    }

    // 海报裁切目标比例（TMDB 规范）
    function posterCropTarget(label) {
        return label === "16:9" ? 16 / 9 : 2 / 3;
    }

    // —— TMDB 图片类型官方规范（/bible/image）：比例、分辨率上下限、格式 ——
    const TMDBH_IMAGE_SPECS = {
        poster: { label: "海报", ratio: 2 / 3, ratioTolerance: 0.02, minWidth: 500, minHeight: 750, maxWidth: 2000, maxHeight: 3000, mime: "image/jpeg", crop: true, scale: true, autoCrop: true },
        backdrop: { label: "背景图", ratio: 16 / 9, ratioTolerance: 0.02, minWidth: 1280, minHeight: 720, maxWidth: 3840, maxHeight: 2160, mime: "image/jpeg", crop: true, scale: true, autoCrop: true },
        still: { label: "剧照", ratio: 16 / 9, ratioTolerance: 0.02, minWidth: 1280, minHeight: 720, maxWidth: 3840, maxHeight: 2160, mime: "image/jpeg", crop: true, scale: true, autoCrop: true },
        logo: { label: "标志", ratio: 0, ratioTolerance: 0, minWidth: 200, minHeight: 50, maxWidth: 2000, maxHeight: 2000, mime: "image/png", crop: false, scale: true, autoCrop: false }
    };

    // 按比例推断图片类型：≈2:3 → 海报，≈16:9 → 背景图/剧照，透明 PNG → 标志
    function inferImageType(width, height, isPng) {
        if (!width || !height) return null;
        if (isPng) {
            // 透明 PNG 且宽明显大于高（典型 logo 形态）优先判标志，其余按比例
            if (width / height >= 1.6) return "logo";
        }
        const ratio = width / height;
        for (const key of ["poster", "backdrop"]) {
            const spec = TMDBH_IMAGE_SPECS[key];
            if (Math.abs(ratio - spec.ratio) <= Math.max(spec.ratioTolerance, 0.05)) return key;
        }
        return null;
    }

    // 按 TMDB 官方规范计算处理参数：比例不对 → 裁剪（CROP，禁止拉伸）；
    // 超过上限 → 等比缩小；低于最低分辨率 → 直接报错（官方禁止放大小图）。
    // options.cropHalf = "left" | "right"：横向图偏转裁剪位（「正面+背面」横向拼图封面
    // 居中裁 2:3 会带拼缝，取右半/左半才能得到单面竖版海报）；纵向上仍居中。
    function computeImageTransform(spec, width, height, options) {
        const opts = options && typeof options === "object" ? options : {};
        const cropHalf = opts.cropHalf === "left" || opts.cropHalf === "right" ? opts.cropHalf : "";
        const problems = [];
        let sx = 0;
        let sy = 0;
        let sw = width;
        let sh = height;
        const notes = [];
        if (spec.autoCrop && spec.ratio && Math.abs(width / height - spec.ratio) > spec.ratioTolerance) {
            if (width / height > spec.ratio) {
                sw = Math.round(height * spec.ratio);
                sx = cropHalf === "right" ? width - sw : cropHalf === "left" ? 0 : Math.round((width - sw) / 2);
            } else {
                sh = Math.round(width / spec.ratio);
                sy = Math.round((height - sh) / 2);
            }
            const cropLabel = cropHalf === "right" ? "取右半裁剪" : cropHalf === "left" ? "取左半裁剪" : "居中裁剪";
            notes.push(`已${cropLabel}为 ${spec.ratio === 2 / 3 ? "2:3" : "16:9"} 比例`);
        }
        let outW = sw;
        let outH = sh;
        if (spec.scale && (outW > spec.maxWidth || outH > spec.maxHeight)) {
            const scale = Math.min(spec.maxWidth / outW, spec.maxHeight / outH);
            outW = Math.max(1, Math.round(outW * scale));
            outH = Math.max(1, Math.round(outH * scale));
            notes.push(`已等比缩小到 ${outW}×${outH}（满足最高分辨率）`);
        }
        if (outW < spec.minWidth || outH < spec.minHeight) {
            problems.push(`分辨率 ${outW}×${outH} 低于最低要求 ${spec.minWidth}×${spec.minHeight}（TMDB 禁止放大低分辨率小图，请更换更清晰的图片）`);
        }
        return { crop: { sx, sy, sw, sh }, width: outW, height: outH, notes, problems };
    }

    // —— 黑边检测（TMDB-Import 的 bordercrop 思路）：逐行/列扫描近黑像素，返回四边可裁宽度 ——
    // data 为 RGBA 像素（getImageData().data）。行/列平均亮度 ≤ meanMax 且最亮像素 ≤ peakMax
    // 视为黑边；单边 < minBar 忽略、最多裁到边长的 maxShare，防止全暗图被裁光。
    function detectImageBlackBars(data, width, height, options) {
        const opts = options && typeof options === "object" ? options : {};
        const meanMax = Number.isFinite(opts.meanMax) ? opts.meanMax : 20;
        const peakMax = Number.isFinite(opts.peakMax) ? opts.peakMax : 48;
        const minBar = Math.max(2, Math.round(Number(opts.minBar) || Math.max(4, Math.round(Math.min(width, height) * 0.02))));
        const maxShare = Number.isFinite(opts.maxShare) ? opts.maxShare : 0.45;
        // 兼容裸 RGBA 数组与 ImageData 形态；不做 instanceof（测试沙箱跨 realm 会失配）
        let pixels = null;
        if (data && typeof data.length === "number" && data.length >= width * height * 4) pixels = data;
        else if (data && data.data && typeof data.data.length === "number" && data.data.length >= width * height * 4) pixels = data.data;
        if (!pixels) return { top: 0, bottom: 0, left: 0, right: 0 };
        const lum = (i) => 0.2126 * pixels[i] + 0.7152 * pixels[i + 1] + 0.0722 * pixels[i + 2];
        const rowDark = (y) => {
            let sum = 0;
            let peak = 0;
            for (let x = 0; x < width; x++) {
                const v = lum((y * width + x) * 4);
                sum += v;
                if (v > peak) peak = v;
            }
            return sum / width <= meanMax && peak <= peakMax;
        };
        const colDark = (x) => {
            let sum = 0;
            let peak = 0;
            for (let y = 0; y < height; y++) {
                const v = lum((y * width + x) * 4);
                sum += v;
                if (v > peak) peak = v;
            }
            return sum / height <= meanMax && peak <= peakMax;
        };
        const capY = Math.max(0, Math.floor(height * maxShare));
        const capX = Math.max(0, Math.floor(width * maxShare));
        let top = 0;
        while (top < capY && rowDark(top)) top++;
        let bottom = 0;
        while (bottom < capY && rowDark(height - 1 - bottom)) bottom++;
        let left = 0;
        while (left < capX && colDark(left)) left++;
        let right = 0;
        while (right < capX && colDark(width - 1 - right)) right++;
        const clamp = (value, dim) => (value >= Math.min(minBar, Math.floor(dim * maxShare)) ? value : 0);
        return { top: clamp(top, height), bottom: clamp(bottom, height), left: clamp(left, width), right: clamp(right, width) };
    }

    // —— 灵活排期生成器 ——
    // TMDB 官方规则（/bible/air-dates）：播出日期是「本地时区当日」的真实日历日（YYYY-MM-DD）；
    // 同一天播多集共用同一日期、集号连续；日期允许是未来（未播出集会被锁定到临近播出）。
    // 星期记号与 Date.getUTCDay 对齐：0=周日 1=周一 … 6=周六；支持「日一二三四五六」「0-7」与英文缩写。
    const TMDBH_WEEKDAY_TOKENS = {
        "日": 0, "天": 0, "sunday": 0, "sun": 0,
        "一": 1, "monday": 1, "mon": 1,
        "二": 2, "tuesday": 2, "tue": 2, "tues": 2,
        "三": 3, "wednesday": 3, "wed": 3,
        "四": 4, "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
        "五": 5, "friday": 5, "fri": 5,
        "六": 6, "saturday": 6, "sat": 6,
        "0": 0, "7": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6
    };

    function tmdbhParseWeekdays(text) {
        const found = [];
        const push = (day) => {
            if (Number.isInteger(day) && day >= 0 && day <= 6 && !found.includes(day)) found.push(day);
        };
        const lower = String(text || "").toLowerCase();
        // 先展开「周一/星期日/周中/周末」等复合词，再按分隔符逐词匹配数字与英文缩写
        const expanded = lower.replace(/周[一二三四五六日天]|星期[一二三四五六日天]|周中|周末/g, (token) => {
            if (token === "周中") {
                [1, 2, 3, 4, 5].forEach(push);
                return " ";
            }
            if (token === "周末") {
                [0, 6].forEach(push);
                return " ";
            }
            push(TMDBH_WEEKDAY_TOKENS[token.slice(-1)]);
            return " ";
        });
        for (const token of expanded.split(/[^a-z0-9\u4e00-\u9fff]+/)) {
            if (!token) continue;
            if (TMDBH_WEEKDAY_TOKENS[token] !== undefined) push(TMDBH_WEEKDAY_TOKENS[token]);
        }
        return found.sort((a, b) => a - b);
    }

    // 排期选项归一化：模式 weekly（每周固定星期）/ interval（固定间隔天）+ 每次集数（单日多集）
    function normalizeScheduleOptions(options) {
        const opts = options && typeof options === "object" ? options : {};
        const startNumber = tmdbhClampInt(opts.startNumber, 1, 2000, 1);
        const count = tmdbhClampInt(opts.count, 0, 500, 0);
        const perSlot = tmdbhClampInt(opts.perSlot, 1, 20, 1);
        const intervalDays = tmdbhClampInt(opts.intervalDays, 1, 60, 7);
        const runtime = tmdbhClampInt(opts.runtime, 0, 600, 0);
        const weekdays = tmdbhParseWeekdays(opts.weekdays);
        const startDate = normalizeAirDate(opts.startDate);
        const template = String(opts.titleTemplate || "").trim() || "第{n}集";
        return {
            pattern: opts.pattern === "interval" ? "interval" : "weekly",
            startNumber, count, perSlot, intervalDays, runtime, weekdays, startDate, template
        };
    }

    // 生成排期分集：weekly 模式从首播日起（含当日，若其星期在列表中）逐日推进挑更新日；
    // interval 模式固定间隔推进。每个更新日发 perSlot 集（单日多集共用日期，集号连续）。
    function buildEpisodeSchedule(options) {
        const opts = normalizeScheduleOptions(options);
        if (!opts.count) return { episodes: [], error: "请填写要生成的集数", meta: null };
        if (!opts.startDate) return { episodes: [], error: "首播日期无效（示例 2026-01-01）", meta: null };
        if (opts.pattern === "weekly" && !opts.weekdays.length) {
            return { episodes: [], error: "每周排期至少选择一个更新日（如：一、四）", meta: null };
        }
        const slotCount = Math.ceil(opts.count / opts.perSlot);
        const dates = [];
        if (opts.pattern === "weekly") {
            const cursor = new Date(`${opts.startDate}T00:00:00Z`);
            let guard = 0;
            while (dates.length < slotCount && guard < 4000) {
                if (opts.weekdays.includes(cursor.getUTCDay())) dates.push(new Date(cursor));
                cursor.setUTCDate(cursor.getUTCDate() + 1);
                guard += 1;
            }
        } else {
            const cursor = new Date(`${opts.startDate}T00:00:00Z`);
            for (let i = 0; i < slotCount; i++) {
                dates.push(new Date(cursor.getTime() + i * opts.intervalDays * 86400000));
            }
        }
        if (dates.length < slotCount) {
            return { episodes: [], error: "排期推进异常（日期范围过大），请检查更新日设置", meta: null };
        }
        const format = (date) => `${date.getUTCFullYear()}-${tmdbhPad2(date.getUTCMonth() + 1)}-${tmdbhPad2(date.getUTCDate())}`;
        const episodes = [];
        let number = opts.startNumber;
        for (const date of dates) {
            for (let k = 0; k < opts.perSlot && episodes.length < opts.count; k++) {
                episodes.push({
                    episodeNumber: number,
                    name: opts.template.replace(/\{n\}/g, String(number)).replace(/\{i\}/g, String(episodes.length + 1)).replace(/\{k\}/g, String(k + 1)),
                    airDate: format(date),
                    overview: "",
                    runtime: opts.runtime,
                    stillUrl: ""
                });
                number += 1;
            }
        }
        const weekdayNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
        const meta = {
            firstDate: episodes.length ? episodes[0].airDate : "",
            lastDate: episodes.length ? episodes[episodes.length - 1].airDate : "",
            summary: episodes.length
                ? (opts.pattern === "weekly"
                    ? `每周 ${opts.weekdays.map((day) => weekdayNames[day]).join("、")} 更新，每次 ${opts.perSlot} 集`
                    : `每 ${opts.intervalDays} 天更新一次，每次 ${opts.perSlot} 集`)
                : ""
        };
        return { episodes, error: "", meta };
    }

    function scoreDoubanSuggestion(subject, query, year) {
        let score = 0;
        const subjectYear = String(subject.year || "").trim();
        if (year && subjectYear === String(year)) {
            score += 20;
        } else if (year && subjectYear) {
            score -= 10;
        }
        const normalizedQuery = normalizeForMatch(query);
        for (const candidateTitle of [subject.title, subject.sub_title]) {
            const normalizedTitle = normalizeForMatch(candidateTitle);
            if (!normalizedTitle || !normalizedQuery) continue;
            if (normalizedTitle === normalizedQuery) {
                score += 12;
            } else if (normalizedTitle.includes(normalizedQuery) || normalizedQuery.includes(normalizedTitle)) {
                score += 6;
            }
        }
        return score;
    }

    function detectPageContext(pathname) {
        const path = String(pathname || "");
        let match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/season\/(\d+)\/episode\/(\d+)\/edit/);
        if (match) return { kind: "episode-edit", mediaType: "tv", tvId: Number(match[1]), seasonNumber: Number(match[2]), episodeNumber: Number(match[3]) };
        // 单集图片页（剧照上传）：URL 类型是 backdrops，页面标题是「剧照」
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/season\/(\d+)\/episode\/(\d+)\/images\/(backdrops|posters|logos)/);
        if (match) return { kind: "episode-images", mediaType: "tv", tvId: Number(match[1]), seasonNumber: Number(match[2]), episodeNumber: Number(match[3]), imageType: match[4] };
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/season\/(\d+)\/edit/);
        if (match) return { kind: "season-edit", mediaType: "tv", tvId: Number(match[1]), seasonNumber: Number(match[2]) };
        // 剧集组编辑/查看页必须先于通用 /edit 规则匹配
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/edit\/episode_group\/([a-f0-9]+)/);
        if (match) return { kind: "episode-group-edit", mediaType: "tv", tvId: Number(match[1]), groupId: match[2] };
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/episode_group\/([a-f0-9]+)/);
        if (match) return { kind: "episode-group-view", mediaType: "tv", tvId: Number(match[1]), groupId: match[2] };
        // 图片上传页（季/剧集/电影），供海报直传
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/season\/(\d+)\/images/);
        if (match) return { kind: "season-images", mediaType: "tv", tvId: Number(match[1]), seasonNumber: Number(match[2]) };
        match = path.match(/^\/(movie|tv)\/(\d+)(?:-[^/]*)?\/images/);
        if (match) return { kind: "images", mediaType: match[1], id: Number(match[2]) };
        match = path.match(/^\/(movie|tv)\/(\d+)(?:-[^/]*)?\/edit/);
        if (match) return { kind: `${match[1]}-edit`, mediaType: match[1], id: Number(match[2]) };
        match = path.match(/^\/(movie|tv)\/new\/?$/);
        if (match) return { kind: `${match[1]}-new`, mediaType: match[1] };
        // 季详情页（/tv/{id}/season/{n}）：此前按未适配处理，面板不注入；v1.0.2 起提供搜索 + 上传海报（目标=该季海报库）
        match = path.match(/^\/tv\/(\d+)(?:-[^/]*)?\/season\/(\d+)\/?$/);
        if (match) return { kind: "season-detail", mediaType: "tv", tvId: Number(match[1]), seasonNumber: Number(match[2]) };
        match = path.match(/^\/(movie|tv)\/(\d+)(?:-[^/]*)?\/?$/);
        if (match) return { kind: `${match[1]}-detail`, mediaType: match[1], id: Number(match[2]) };
        return { kind: "other" };
    }

    // 上下文感知：悬浮窗默认只有「搜索」；批量单集只在季编辑器、剧集组只在剧集组页、上传图片只在图片上传页出现
    function tmdbhViewsForContext(context) {
        const kind = context.kind;
        if (kind === "season-edit") return ["episodes", "search"];  // 季编辑器 → 集编辑器 + 搜索
        if (kind === "episode-group-view" || kind === "episode-group-edit") return ["groups"]; // 剧集组只在剧集组页提供
        if (kind === "images" || kind === "season-images" || kind === "episode-images") return ["upload"];
        if (kind === "other") return null; // 未适配页面不注入
        return ["search"]; // 新增/编辑/详情等其余适配页：只要搜索
    }

    // 从页面标题解析「标题 + 年份」：TMDB 标题形如「名称 (2026)」「剧集 (TV Series 2026)」，
    // 旧版只认「(2026)」导致剧集详情页复制 tmdbid 标记时丢年份。
    function tmdbhParsePageTitle(rawTitle) {
        const text = String(rawTitle || "").replace(/\s*[—-]\s*The Movie Database.*$/i, "").trim();
        const match = text.match(/^(.*?)[（(][^（）()]*?(\d{4})[^（）()]*?[)）]/);
        if (match) return { title: match[1].trim(), year: Number(match[2]) };
        return { title: text, year: 0 };
    }


    // src/fields.js —— 编辑器语言推断（v1.2.1 起表单回写已移除，批量单集提交仍需语言标记）
    function detectEditorLanguage(text) {
        const match = String(text || "").match(/\(\s*([a-z]{2,3}-[A-Za-z]{2,4})\s*\)/);
        return match ? match[1] : "";
    }

    // src/datasource.js —— 统一数据源抽象（纯逻辑，供测试切片）
    // 设计：所有来源（豆瓣 / IMDb / 粘贴文本 / 未来新源）经适配器归一为同一份
    // 「统一条目记录」（unified record），对照填写与右栏展示只认这份结构；
    // 新数据源通过 registerDataSource 注册（id 唯一），面板自动获得新入口。
    function createUnifiedRecord(patch) {
        return Object.assign({
            source: "",
            sourceId: "",
            url: "",
            title: "",
            originalTitle: "",
            year: 0,
            date: "",
            runtime: 0,
            overview: "",
            genres: [],
            countries: [],
            aliases: [],
            directors: [],
            writers: [],
            cast: [],
            companies: [],
            networks: [],
            languages: [],
            rating: "",
            episodeCount: 0,
            poster: ""
        }, patch && typeof patch === "object" ? patch : {});
    }

    function toList(value) {
        if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
        return splitAliases(value);
    }

    function castToList(value) {
        // cast 允许传名字数组或 {name, character} 数组；字符串里支持「名字 饰 角色」
        const list = Array.isArray(value) ? value : splitAliases(value);
        return list.map((item) => {
            if (item && typeof item === "object") {
                return { name: String(item.name || "").trim(), character: String(item.character || "").trim() };
            }
            const text = String(item || "").trim();
            const match = text.match(/^(.*?)\s*[饰:：]\s*(.+)$/);
            return match ? { name: match[1].trim(), character: match[2].trim() } : { name: text, character: "" };
        }).filter((item) => item.name);
    }

    // 豆瓣详情（parseDoubanDetailHtml / parseDoubanRexxarJson 的产物）→ 统一记录
    function normalizeDoubanDetail(detail) {
        const d = detail && typeof detail === "object" ? detail : {};
        return createUnifiedRecord({
            source: "douban",
            sourceId: String(d.doubanId || ""),
            url: String(d.doubanUrl || (d.doubanId ? `https://movie.douban.com/subject/${d.doubanId}/` : "")),
            title: String(d.title || "").trim(),
            originalTitle: String(d.originalTitle || "").trim(),
            year: Number(d.year) || 0,
            date: normalizeAirDate(d.date || ""),
            runtime: Number(d.runtime) || 0,
            overview: String(d.overview || "").trim(),
            genres: toList(d.genres),
            countries: toList(d.countries),
            aliases: toList(d.aliases),
            directors: toList(d.directors),
            writers: toList(d.writers),
            cast: castToList(d.cast || d.actors),
            companies: toList(d.companies),
            languages: toList(d.languages),
            rating: String(d.rating || ""),
            episodeCount: Number(d.episodeCount) || 0,
            poster: String(d.poster || "")
        });
    }

    // 百度百科详情（parseBaikeDetailHtml 的产物）→ 统一记录
    function normalizeBaikeDetail(detail) {
        const d = detail && typeof detail === "object" ? detail : {};
        return createUnifiedRecord({
            source: "baike",
            sourceId: String(d.baikeId || ""),
            url: String(d.baikeUrl || (d.baikeId ? `https://baike.baidu.com/item/${encodeURIComponent(d.title || "")}/${d.baikeId}` : "")),
            title: String(d.title || "").trim(),
            originalTitle: String(d.originalTitle || "").trim(),
            year: Number(d.year) || parseYearValue(d.date || ""),
            date: normalizeAirDate(d.date || ""),
            runtime: Number(d.runtime) || 0,
            overview: String(d.overview || "").trim(),
            genres: toList(d.genres),
            countries: toList(d.countries),
            aliases: toList(d.aliases),
            directors: toList(d.directors),
            writers: toList(d.writers),
            cast: castToList(d.cast),
            companies: toList(d.companies),
            networks: toList(d.networks),
            languages: toList(d.languages),
            episodeCount: Number(d.episodeCount) || 0,
            poster: String(d.poster || "")
        });
    }

    // TMDB API 详情（movie/tv append credits/external_ids/images）→ 统一记录
    function normalizeTmdbDetail(data, mediaType) {
        const d = data && typeof data === "object" ? data : {};
        const joinNames = (value) => (Array.isArray(value) ? value.map((item) => String((item && (item.name || item.title)) || "").trim()).filter(Boolean) : []);
        const cast = Array.isArray(d.credits && d.credits.cast) ? d.credits.cast : [];
        const crew = Array.isArray(d.credits && d.credits.crew) ? d.credits.crew : [];
        return createUnifiedRecord({
            source: "tmdb",
            sourceId: String(d.id || ""),
            url: d.id ? `https://www.themoviedb.org/${mediaType === "movie" ? "movie" : "tv"}/${d.id}` : "",
            title: String(d.title || d.name || "").trim(),
            originalTitle: String(d.original_title || d.original_name || "").trim(),
            year: parseYearValue(d.release_date || d.first_air_date || ""),
            date: normalizeAirDate(d.release_date || d.first_air_date || ""),
            runtime: Number(d.runtime) || (Array.isArray(d.episode_run_time) && d.episode_run_time.length ? Number(d.episode_run_time[0]) : 0),
            overview: String(d.overview || "").trim(),
            genres: joinNames(d.genres),
            countries: joinNames(d.production_countries),
            aliases: [],
            directors: crew.filter((item) => item.job === "Director").map((item) => String(item.name || "").trim()).filter(Boolean),
            writers: crew.filter((item) => item.job === "Writer" || item.department === "Writing").map((item) => String(item.name || "").trim()).filter(Boolean),
            cast: cast.slice(0, 30).map((item) => ({ name: String(item.name || "").trim(), character: String(item.character || "").trim() })).filter((item) => item.name),
            companies: joinNames(d.production_companies),
            networks: joinNames(d.networks),
            languages: Array.isArray(d.spoken_languages) ? d.spoken_languages.map((item) => String(item.english_name || item.name || "").trim()).filter(Boolean) : (Array.isArray(d.languages) ? d.languages.map(String) : []),
            rating: Number(d.vote_average) > 0 ? Number(d.vote_average).toFixed(1) : "",
            episodeCount: Number(d.number_of_episodes) || 0,
            poster: d.poster_path ? `https://image.tmdb.org/t/p/w342${d.poster_path}` : ""
        });
    }

    // 粘贴文本解析（parseEntryText）→ 统一记录
    function normalizeParsedEntry(parsed) {
        const p = parsed && typeof parsed === "object" ? parsed : {};
        return createUnifiedRecord({
            source: "text",
            title: String(p.title || "").trim(),
            originalTitle: String(p.originalTitle || "").trim(),
            year: Number(p.year) || 0,
            date: normalizeAirDate(p.date || ""),
            runtime: Number(p.runtime) || 0,
            overview: String(p.overview || "").trim(),
            genres: toList(p.genres),
            countries: toList(p.countries),
            aliases: toList(p.aliases),
            tagline: String(p.tagline || "").trim(),
            homepage: String(p.homepage || "").trim(),
            imdb: /^tt\d+$/i.test(String(p.imdb || "")) ? String(p.imdb).toUpperCase() : "",
            productionCode: String(p.productionCode || "").trim(),
            directors: toList(p.directors),
            cast: castToList(p.cast)
        });
    }

    // 统一记录 → 扁平值（供复制与展示）
    function unifiedToEntryValues(record) {
        const rec = record && typeof record === "object" ? record : {};
        return {
            title: String(rec.title || "").trim(),
            originalTitle: String(rec.originalTitle || "").trim(),
            date: normalizeAirDate(rec.date || ""),
            runtime: Number(rec.runtime) > 0 ? Math.round(Number(rec.runtime)) : 0,
            overview: String(rec.overview || "").trim(),
            genres: toList(rec.genres).join("/"),
            countries: toList(rec.countries).join("/"),
            aliases: toList(rec.aliases).join("/"),
            tagline: String(rec.tagline || "").trim(),
            homepage: String(rec.url && /^https?:\/\//.test(rec.url) && !/douban\.com|themoviedb\.org/.test(rec.url) ? rec.url : rec.homepage || "").trim(),
            imdb: /^tt\d+$/i.test(String(rec.imdb || "")) ? String(rec.imdb).toUpperCase() : "",
            productionCode: String(rec.productionCode || "").trim(),
            year: Number(rec.year) || 0,
            poster: String(rec.poster || ""),
            rating: String(rec.rating || "")
        };
    }

    // 条目的「名称 (年份)」标准格式（复制与展示共用）
    function recordTitleYear(rec, useOriginal) {
        const title = String((useOriginal ? (rec.originalTitle || rec.title) : rec.title) || "").trim();
        const year = Number(rec.year) || parseYearValue(rec.date || "");
        return `${title}${year ? ` (${year})` : ""}`;
    }

    // 「复制全部信息」的纯文本块：把统一记录里的关键信息整理成可直接粘贴的清单
    function buildRecordText(record) {
        const values = unifiedToEntryValues(record);
        const cast = Array.isArray(record.cast) ? record.cast.map((item) => (item && item.character ? `${item.name} 饰 ${item.character}` : item && item.name)).filter(Boolean) : [];
        const lines = [
            `标题：${values.title || ""}`,
            values.originalTitle ? `原名：${values.originalTitle}` : "",
            values.year ? `年份：${values.year}` : "",
            values.date ? `日期：${values.date}` : "",
            values.runtime ? `片长：${Math.round(Number(values.runtime))} 分钟` : "",
            toList(values.genres).length ? `类型：${toList(values.genres).join("/")}` : "",
            toList(values.countries).length ? `制片国家/地区：${toList(values.countries).join("/")}` : "",
            values.rating ? `评分：${values.rating}` : "",
            record.episodeCount ? `集数：${record.episodeCount}` : "",
            toList(record.aliases).length ? `别名：${toList(record.aliases).join("/")}` : "",
            toList(record.directors).length ? `导演：${toList(record.directors).join("、")}` : "",
            toList(record.writers).length ? `编剧：${toList(record.writers).join("、")}` : "",
            cast.length ? `主演：${cast.join("、")}` : "",
            toList(record.companies).length ? `出品方：${toList(record.companies).join("、")}` : "",
            toList(record.networks).length ? `播出平台：${toList(record.networks).join("、")}` : "",
            toList(record.languages).length ? `语言：${toList(record.languages).join("、")}` : "",
            values.imdb ? `IMDb：${values.imdb}` : "",
            record.url ? `链接：${record.url}` : "",
            values.overview ? `简介：${values.overview}` : ""
        ];
        return lines.filter((line) => line && !/[:：]\s*$/.test(line)).join("\n");
    }

    function createDataSourceRegistry() {
        const byId = new Map();
        return {
            register(adapter) {
                if (!adapter || !adapter.id) throw new Error("数据源适配器缺少 id");
                byId.set(String(adapter.id), adapter);
                return Array.from(byId.keys());
            },
            get(id) {
                return byId.get(String(id)) || null;
            },
            list() {
                return Array.from(byId.values());
            }
        };
    }

    function createEpisodeActionRegistry() {
        // 批量操作注册表：{ id, title, when?(env) → bool, run(env) → Promise<void|string> }
        // env 由集编辑器注入：{ tvId, seasonNumber, episodes, selectedNumbers, existingIndex,
        //   state, refresh(), toast(), uploadEpisodeStill(), config }
        const actions = [];
        return {
            register(action) {
                if (!action || !action.id || typeof action.run !== "function") throw new Error("批量操作缺少 id 或 run");
                const existing = actions.findIndex((item) => item.id === action.id);
                if (existing >= 0) actions[existing] = action;
                else actions.push(action);
                return actions.map((item) => item.id);
            },
            list(env) {
                return actions.filter((action) => {
                    try {
                        return typeof action.when === "function" ? Boolean(action.when(env)) : true;
                    } catch (err) {
                        return true;
                    }
                });
            },
            run(id, env) {
                const action = actions.find((item) => item.id === id);
                if (!action) return Promise.reject(new Error(`未注册的批量操作：${id}`));
                return Promise.resolve().then(() => action.run(env));
            }
        };
    }


    // src/episodes.js —— 季编辑器内部接口客户端 + 分集缩略图上传契约（纯 payload 构建供测试切片）
    function seasonEpisodesUrl(tvId, seasonNumber) {
        return `/tv/${Number(tvId)}/season/${Number(seasonNumber)}/remote/episodes`;
    }

    function episodePrimaryFactsUrl(episodeId, tvId, language) {
        const params = new URLSearchParams({ language: language || "zh-CN", series_id: String(Number(tvId)), translate: "false" });
        return `/tv/episode/${Number(episodeId)}/remote/primary_facts?${params.toString()}`;
    }

    // 单集剧照页：URL 里的图片类型是 backdrops（页面标题为「剧照」，内嵌 kendo 上传配置）
    function episodeStillsPageUrl(tvId, seasonNumber, episodeNumber) {
        return `/tv/${Number(tvId)}/season/${Number(seasonNumber)}/episode/${Number(episodeNumber)}/images/backdrops`;
    }

    // 季图片页（官方 Media → Posters）：季海报/背景图在这里上传，助手的直传面板也挂在这类页面
    function seasonImagesUrl(tvId, seasonNumber) {
        return `/tv/${Number(tvId)}/season/${Number(seasonNumber)}/images/posters`;
    }

    // 分集缩略图直传契约（与官方单集图片页 kendo Upload 一致）：
    // multipart POST /image，media_id = 单集 bson_id，media_type = TvEpisode，type = still
    function buildStillUploadFields(bsonId) {
        return {
            media_id: String(bsonId || ""),
            media_type: "TvEpisode",
            type: "still",
            translate: "false"
        };
    }

    function buildEpisodePayload(episode, seasonNumber) {
        const payload = {
            episode_number: Number(episode.episodeNumber) || 0,
            locked_fields: [],
            season_number: Number(seasonNumber) || 0,
            new: true
        };
        if (episode.name) payload.name = episode.name;
        if (episode.overview) payload.overview = episode.overview;
        if (episode.airDate) payload.air_date = episode.airDate;
        if (episode.runtime) payload.runtime = Number(episode.runtime);
        return payload;
    }

    // 覆盖已有集：必须带上 TMDB 内部记录的 id/bson_id 等元数据，且不能带 new 标记，
    // 走 /tv/episode/{id}/remote/primary_facts 更新接口；未填字段回退到线上现值。
    function buildEpisodeUpdatePayload(episode, tvId, current) {
        const raw = current && typeof current === "object" ? current : {};
        const pick = (key, fallback) => (raw[key] === undefined || raw[key] === null ? fallback : raw[key]);
        const payload = {
            episode_number: Number(episode.episodeNumber) || 0,
            locked_fields: [],
            id: Number(raw.id) || 0,
            season_number: pick("season_number", 0),
            show_id: Number(tvId) || 0,
            bson_id: String(raw.bson_id || ""),
            production_code: pick("production_code", ""),
            vote_average: pick("vote_average", 0),
            vote_count: pick("vote_count", 0)
        };
        payload.name = String(episode.name || "").trim() || String(raw.name || "");
        payload.overview = String(episode.overview || "").trim() || String(raw.overview || "");
        payload.air_date = String(episode.airDate || "").trim() || String(raw.air_date || "");
        if (episode.runtime !== undefined && episode.runtime !== null && String(episode.runtime).trim() !== "") {
            payload.runtime = Number(episode.runtime);
        } else {
            payload.runtime = raw.runtime === undefined ? null : raw.runtime;
        }
        return payload;
    }

    function normalizeRemoteEpisodes(data) {
        const list = Array.isArray(data) ? data : Array.isArray(data && data.episodes) ? data.episodes : [];
        return list
            .filter((item) => item && typeof item === "object")
            .map((item) => ({
                id: item.id,
                episodeNumber: Number(item.episode_number) || 0,
                name: String(item.name || ""),
                airDate: normalizeAirDate(item.air_date || ""),
                overview: String(item.overview || "").replace(/\s+/g, " ").trim(),
                runtime: Number(item.runtime) || 0,
                bsonId: String(item.bson_id || "")
            }))
            .filter((item) => item.episodeNumber > 0);
    }

    // 把 GET remote/episodes 的原始响应按集号建索引，覆盖提交/缩略图上传时用它补齐 id/bson_id 等元数据
    function buildRemoteEpisodeIndex(data) {
        const list = Array.isArray(data) ? data : Array.isArray(data && data.episodes) ? data.episodes : [];
        const index = {};
        for (const item of list) {
            if (!item || typeof item !== "object") continue;
            const number = Number(item.episode_number) || 0;
            if (number > 0) index[number] = item;
        }
        return index;
    }

    const tmdbhSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const TMDBH_DONE_KEY = "Tmdb.Helper.EpisodesDone";

    // 断点续传：记录本脚本已成功提交过的集号，刷新/换页后继续防重复
    function loadDoneEpisodes(storage, tvId, seasonNumber) {
        try {
            const all = JSON.parse(storage.get(TMDBH_DONE_KEY) || "{}");
            const list = all && all[`${Number(tvId)}:${Number(seasonNumber)}`];
            return new Set((Array.isArray(list) ? list : []).map(Number).filter((n) => Number.isFinite(n) && n > 0));
        } catch (err) {
            return new Set();
        }
    }

    function recordDoneEpisodes(storage, tvId, seasonNumber, numbers) {
        try {
            const all = JSON.parse(storage.get(TMDBH_DONE_KEY) || "{}");
            const key = `${Number(tvId)}:${Number(seasonNumber)}`;
            const merged = new Set(Array.isArray(all && all[key]) ? all[key].map(Number).filter((n) => Number.isFinite(n)) : []);
            for (const number of numbers) merged.add(Number(number));
            all[key] = Array.from(merged).slice(-1000);
            storage.set(TMDBH_DONE_KEY, JSON.stringify(all));
        } catch (err) {
            /* 忽略 */
        }
    }

    async function fetchRemoteEpisodesData(tvId, seasonNumber) {
        const response = await fetch(`${seasonEpisodesUrl(tvId, seasonNumber)}?translate=false`, {
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01"
            },
            credentials: "same-origin"
        });
        if (!response.ok) throw new Error(`读取已有单集失败（HTTP ${response.status}）`);
        const text = await response.text();
        try {
            return JSON.parse(text);
        } catch (err) {
            return [];
        }
    }

    async function listRemoteEpisodes(tvId, seasonNumber) {
        return normalizeRemoteEpisodes(await fetchRemoteEpisodesData(tvId, seasonNumber));
    }

    function parseEpisodeMutationResult(response, text) {
        if (response.status === 401 || response.status === 403) {
            const err = new Error(`未登录或无权限（HTTP ${response.status}）`);
            err.auth = true;
            err.status = response.status;
            throw err;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status} ${text.slice(0, 120)}`);
        let data = null;
        try {
            data = JSON.parse(text);
        } catch (err) {
            const authErr = new Error("响应不是 JSON，可能已退出登录或接口已变化");
            authErr.auth = true;
            throw authErr;
        }
        if (data && data.failure) {
            const errors = Array.isArray(data.failure.errors) ? data.failure.errors.join("；") : JSON.stringify(data.failure);
            throw new Error(`TMDB 校验失败：${String(errors).slice(0, 200)}`);
        }
        if (!data || typeof data !== "object") {
            throw new Error("空响应，请稍后重试");
        }
        return data;
    }

    async function postRemoteEpisode(tvId, seasonNumber, language, payload) {
        const params = new URLSearchParams({ language: language || "zh-CN", translate: "false" });
        const response = await fetch(`${seasonEpisodesUrl(tvId, seasonNumber)}?${params.toString()}`, {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            credentials: "same-origin",
            body: `data=${encodeURIComponent(JSON.stringify(payload))}`
        });
        const text = await response.text();
        return parseEpisodeMutationResult(response, text);
    }

    // 覆盖已有集：官方编辑器对单集走 primary_facts 更新接口
    async function postEpisodeUpdate(tvId, episodeId, language, payload) {
        const response = await fetch(episodePrimaryFactsUrl(episodeId, tvId, language), {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            credentials: "same-origin",
            body: `data=${encodeURIComponent(JSON.stringify(payload))}`
        });
        const text = await response.text();
        return parseEpisodeMutationResult(response, text);
    }

    // —— 剧集组内部接口运行时（同源带登录态；写操作与官网 kendo 一致：data=JSON） ——
    async function tmdbhInternalJson(path, method, payload) {
        const response = await fetch(path, {
            method: method || "GET",
            headers: Object.assign(
                { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01" },
                payload ? { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8" } : {}
            ),
            credentials: "same-origin",
            body: payload ? `data=${encodeURIComponent(JSON.stringify(payload))}` : undefined
        });
        const text = await response.text();
        if (response.status === 401 || response.status === 403) {
            const err = new Error(`未登录或无权限（HTTP ${response.status}）`);
            err.auth = true;
            throw err;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status} ${text.slice(0, 120)}`);
        if (!text.trim()) return null;
        try {
            return JSON.parse(text);
        } catch (err) {
            throw new Error("响应不是 JSON（可能未登录或接口已变化）");
        }
    }

    function checkGroupMutation(data) {
        if (data && data.failure) {
            const errors = Array.isArray(data.failure.errors) ? data.failure.errors.join("；") : JSON.stringify(data.failure);
            throw new Error(`TMDB 校验失败：${String(errors).slice(0, 200)}`);
        }
        return data;
    }

    async function fetchGroupsInternal(tvId) {
        const data = await tmdbhInternalJson(episodeGroupsRemoteUrl(tvId));
        return Array.isArray(data) ? data : [];
    }

    async function fetchSubGroupsInternal(tvId, groupId) {
        const data = await tmdbhInternalJson(episodeGroupSubGroupsUrl(tvId, groupId));
        if (Array.isArray(data)) return data;
        return Array.isArray(data && data.groups) ? data.groups : [];
    }

    async function fetchSubGroupEpisodesInternal(tvId, groupId, subGroupId) {
        const data = await tmdbhInternalJson(episodeGroupSubGroupEpisodesUrl(tvId, groupId, subGroupId));
        if (Array.isArray(data)) return data;
        return Array.isArray(data && data.episodes) ? data.episodes : [];
    }

    // 官方编辑器契约：新增/重排都是 PUT data={"models":[...]}（upsert，media_id 为键）
    async function saveSubGroupEpisodes(tvId, groupId, subGroupId, episodes) {
        const data = await tmdbhInternalJson(episodeGroupSubGroupEpisodesUrl(tvId, groupId, subGroupId), "PUT", buildSubGroupEpisodesPayload(episodes));
        return checkGroupMutation(data);
    }

    async function removeSubGroupEpisode(tvId, groupId, subGroupId, item) {
        const payload = {
            models: [{
                media_id: String(item.media_id || ""),
                season_number: Number(item.season_number) || 0,
                episode_number: Number(item.episode_number) || 0,
                name: String(item.name || ""),
                order: Number(item.order) || 0
            }]
        };
        const data = await tmdbhInternalJson(episodeGroupSubGroupEpisodesUrl(tvId, groupId, subGroupId), "DELETE", payload);
        return checkGroupMutation(data);
    }

    async function batchAddEpisodes(tvId, seasonNumber, language, episodes, options, callbacks) {
        const delayMs = options && Number.isFinite(Number(options.delayMs)) ? Number(options.delayMs) : 1000;
        const retries = options && Number.isFinite(Number(options.retries)) ? Number(options.retries) : 1;
        const existingIndex = (options && options.existingIndex) || {};
        const total = episodes.length;
        for (let i = 0; i < total; i++) {
            if (callbacks && typeof callbacks.shouldStop === "function" && callbacks.shouldStop()) break;
            const episode = episodes[i];
            if (callbacks && callbacks.onStart) callbacks.onStart(i, total, episode);
            const current = existingIndex[episode.episodeNumber];
            let lastError = null;
            for (let attempt = 0; attempt <= retries; attempt++) {
                try {
                    if (current && Number(current.id) > 0) {
                        await postEpisodeUpdate(tvId, Number(current.id), language, buildEpisodeUpdatePayload(episode, tvId, current));
                    } else {
                        await postRemoteEpisode(tvId, seasonNumber, language, buildEpisodePayload(episode, seasonNumber));
                    }
                    lastError = null;
                    break;
                } catch (err) {
                    lastError = err;
                    if (err && err.auth) break;
                    if (attempt < retries) await tmdbhSleep(800);
                }
            }
            if (callbacks && callbacks.onResult) callbacks.onResult(i, total, episode, lastError);
            if (lastError && lastError.auth) break;
            if (i < total - 1) await tmdbhSleep(delayMs);
        }
    }

    // —— 剧集组内部接口（登录态可用；增删改走官网同款 kendo 端点，body 为 data=JSON） ——
    function episodeGroupsRemoteUrl(tvId) {
        return `/tv/${Number(tvId)}/remote/episode_groups?translate=false`;
    }

    function episodeGroupSubGroupsUrl(tvId, groupId) {
        return `/tv/${Number(tvId)}/remote/episode_group/${encodeURIComponent(groupId)}/groups?translate=false`;
    }

    function episodeGroupSubGroupEpisodesUrl(tvId, groupId, subGroupId) {
        return `/tv/${Number(tvId)}/remote/episode_group/${encodeURIComponent(groupId)}/${encodeURIComponent(subGroupId)}/episodes`;
    }

    function buildEpisodeGroupWritePayload(group, includeId) {
        const payload = {
            name: String(group.name || "").trim(),
            description: String(group.description || "").trim(),
            type: Number(group.type) >= 1 && Number(group.type) <= 6 ? Number(group.type) : 2
        };
        if (includeId) payload.id = String(group.id || "");
        return payload;
    }

    function buildSubGroupWritePayload(sub, includeId, defaultOrder) {
        const payload = {
            name: String(sub.name || "").trim(),
            order: Number.isFinite(Number(sub.order)) && Number(sub.order) >= 0 ? Math.round(Number(sub.order)) : (Number(defaultOrder) || 0),
            episode_count: Number(sub.episode_count) || 0
        };
        if (includeId) payload.id = String(sub.id || "");
        return payload;
    }

    // 子组单集成员保存载荷：官方编辑器走 PUT data={"models":[...]}（增删改同一个端点）
    function buildSubGroupEpisodesPayload(episodes) {
        return {
            models: (Array.isArray(episodes) ? episodes : []).map((item, index) => ({
                media_id: String(item.media_id || item.bson_id || ""),
                season_number: Number(item.season_number) || 0,
                episode_number: Number(item.episode_number) || 0,
                name: String(item.name || ""),
                order: Number.isFinite(Number(item.order)) ? Math.round(Number(item.order)) : index
            }))
        };
    }

    // 子组内按季/集号升序重排，order 重写为数组下标
    function sortSubGroupEpisodes(episodes) {
        return (Array.isArray(episodes) ? episodes.slice() : [])
            .sort((a, b) => (Number(a.season_number) || 0) - (Number(b.season_number) || 0) || (Number(a.episode_number) || 0) - (Number(b.episode_number) || 0))
            .map((item, index) => Object.assign({}, item, { order: index }));
    }

    // 从季编辑器原始单集列表里挑出未入组的集，供勾选添加
    function filterSubGroupCandidates(seasonEpisodes, existingKeys) {
        const taken = new Set(Array.isArray(existingKeys) ? existingKeys.map((key) => String(key)) : []);
        return (Array.isArray(seasonEpisodes) ? seasonEpisodes : []).filter((item) => {
            const mediaId = String(item.media_id || item.bson_id || "");
            return mediaId && !taken.has(mediaId);
        });
    }

    // src/net.js —— GM_xmlhttpRequest Promise 封装（运行时）
    function tmdbhGmRequest(options) {
        return new Promise((resolve, reject) => {
            if (typeof GM_xmlhttpRequest !== "function") {
                reject(new Error("当前环境缺少 GM_xmlhttpRequest 权限，无法跨域请求"));
                return;
            }
            GM_xmlhttpRequest(
                Object.assign({}, options, {
                    onload: resolve,
                    onerror: () => reject(new Error("网络请求失败")),
                    ontimeout: () => reject(new Error("请求超时"))
                })
            );
        });
    }


    // src/douban.js —— 豆瓣数据源（自本地后端 tmdb.py 移植：搜索建议 + 打分匹配 + 节流 + 缓存）
    const TMDBH_DOUBAN_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://movie.douban.com/"
    };
    // m.douban.com rexxar 接口：Referer 必须是 m 站，否则返回 400
    const TMDBH_REXXAR_HEADERS = {
        "User-Agent": TMDBH_DOUBAN_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://m.douban.com/"
    };
    // 被风控时豆瓣会把请求 302 到安全验证页，HTTP 仍是 200，只能靠最终 URL 识别
    const TMDBH_DOUBAN_BLOCKED_URL_RE = /sec\.douban\.com|\/accounts\/login|\/secsdk\//;
    const TMDBH_DOUBAN_CACHE_TTL = 7200 * 1000;
    const TMDBH_DOUBAN_CACHE_MAX = 200;

    const tmdbhDoubanCache = new Map();
    let tmdbhDoubanLastRequestAt = 0;

    function tmdbhDoubanCacheGet(key) {
        const entry = tmdbhDoubanCache.get(key);
        if (!entry) return undefined;
        if (Date.now() - entry.at > TMDBH_DOUBAN_CACHE_TTL) {
            tmdbhDoubanCache.delete(key);
            return undefined;
        }
        return entry.value;
    }

    function tmdbhDoubanCacheSet(key, value) {
        if (tmdbhDoubanCache.size >= TMDBH_DOUBAN_CACHE_MAX) {
            const oldest = tmdbhDoubanCache.keys().next().value;
            if (oldest !== undefined) tmdbhDoubanCache.delete(oldest);
        }
        tmdbhDoubanCache.set(key, { at: Date.now(), value });
    }

    async function tmdbhDoubanThrottle(minIntervalMs) {
        const interval = tmdbhClampInt(minIntervalMs, 500, 10000, 2000);
        const elapsed = Date.now() - tmdbhDoubanLastRequestAt;
        if (elapsed < interval) await tmdbhSleep(interval - elapsed);
        tmdbhDoubanLastRequestAt = Date.now();
    }

    async function tmdbhDoubanFetch(url, config, headers) {
        if (config && config.douban && config.douban.enabled === false) {
            throw new Error("豆瓣功能已在设置中关闭");
        }
        await tmdbhDoubanThrottle(config && config.douban ? config.douban.minIntervalMs : 2000);
        const response = await tmdbhGmRequest({ method: "GET", url, headers: headers || TMDBH_DOUBAN_HEADERS, timeout: 20000 });
        if (response.status >= 400) throw new Error(`豆瓣返回 HTTP ${response.status}`);
        if (TMDBH_DOUBAN_BLOCKED_URL_RE.test(String(response.finalUrl || ""))) {
            throw new Error("请求被豆瓣重定向到安全验证页（风控）");
        }
        return response;
    }

    async function tmdbhDoubanGetJSON(url, params, config) {
        const query = params ? `?${new URLSearchParams(params).toString()}` : "";
        const cacheKey = `json:${url}${query}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        const response = await tmdbhDoubanFetch(url + query, config);
        let data = null;
        try {
            data = JSON.parse(response.responseText);
        } catch (err) {
            throw new Error("豆瓣响应不是 JSON（可能被风控，稍后再试）");
        }
        tmdbhDoubanCacheSet(cacheKey, data);
        return data;
    }

    async function tmdbhDoubanGetText(url, config, validate) {
        const cacheKey = `text:${url}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        const response = await tmdbhDoubanFetch(url, config);
        const text = String(response.responseText || "");
        // 校验不过（疑似风控/验证页）时直接返回但不缓存，避免坏结果占住缓存
        if (typeof validate === "function" && !validate(text)) return text;
        tmdbhDoubanCacheSet(cacheKey, text);
        return text;
    }

    async function searchDoubanSubjects(query, config) {
        const keyword = String(query || "").trim();
        if (!keyword) return [];
        const data = await tmdbhDoubanGetJSON("https://movie.douban.com/j/subject_suggest", { q: keyword }, config);
        const list = Array.isArray(data) ? data : [];
        const mediaLabel = { movie: "电影", tv: "剧集" };
        return list
            .filter((item) => item && /^\d+$/.test(String(item.id || "")))
            .map((item) => {
                // suggest 接口的类型字段是 type（movie/tv），部分老接口叫 media_type
                const mediaType = String(item.media_type || item.type || "").trim().toLowerCase();
                return {
                    id: String(item.id),
                    title: String(item.title || "").trim(),
                    subTitle: String(item.sub_title || "").trim(),
                    year: String(item.year || "").trim(),
                    mediaType: mediaLabel[mediaType] || mediaType,
                    url: String(item.url || "").trim()
                };
            });
    }

    function pickBestDoubanSubject(subjects, query, year) {
        if (!subjects.length) return null;
        let best = null;
        let bestScore = -Infinity;
        subjects.forEach((subject, index) => {
            const score = scoreDoubanSuggestion(subject, query, year) - index * 0.01;
            if (score > bestScore) {
                best = subject;
                bestScore = score;
            }
        });
        return best;
    }

    // 搜索结果里的条目链接有两种形态：明文 URL，以及 douban.com/link2 跳转里
    // 的百分号编码（movie.douban.com%2Fsubject%2F123），两种都要能提取 ID
    function tmdbhMatchDoubanSubjectId(text) {
        const match = String(text || "").match(/movie\.douban\.com(?:%2F|\/)subject(?:%2F|\/)(\d+)/i);
        return match ? match[1] : "";
    }

    async function searchDoubanByImdb(imdbId, config) {
        const id = String(imdbId || "").trim();
        if (!/^tt\d+$/.test(id)) return null;
        const cacheKey = `imdb:${id}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        const response = await tmdbhDoubanFetch(`https://www.douban.com/search?cat=1002&q=${encodeURIComponent(id)}`, config);
        const subjectId = tmdbhMatchDoubanSubjectId(response.responseText);
        const result = subjectId ? { id: subjectId } : null;
        tmdbhDoubanCacheSet(cacheKey, result);
        return result;
    }

    function parseDoubanDetailHtml(html, doubanId) {
        const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
        const titleNode = doc.querySelector('span[property="v:itemreviewed"]');
        const title = titleNode ? titleNode.textContent.trim() : "";
        const yearNode = doc.querySelector("#content h1 .year");
        const year = parseYearValue(yearNode ? yearNode.textContent : "");
        const overviewNodes = Array.from(doc.querySelectorAll('span[property="v:summary"]'));
        const overview = collapseSummary(overviewNodes.map((node) => node.textContent.trim()).filter(Boolean).join("\n"));
        const ratingNode = doc.querySelector('strong[property="v:average"]');
        const picNode = doc.querySelector("#mainpic img") || doc.querySelector('meta[property="og:image"]');

        const info = doc.querySelector("#info");
        let infoText = "";
        if (info) {
            const clone = info.cloneNode(true);
            clone.querySelectorAll("br").forEach((br) => br.replaceWith("\n"));
            infoText = clone.textContent || "";
        }
        let date = "";
        const dateMatch = infoText.match(/(?:首播|播出日期|上映日期)\s*[::：]?\s*([\d\s\-/.年月]+)/);
        if (dateMatch) date = normalizeAirDate(dateMatch[1]);
        let runtime = 0;
        const runtimeMatch = infoText.match(/单集片长\s*[::：]?\s*([^\n]+)/);
        if (runtimeMatch) {
            const numbers = [];
            const re = /(\d{1,4})\s*分钟/g;
            let m;
            while ((m = re.exec(runtimeMatch[1])) !== null) numbers.push(Number(m[1]));
            if (numbers.length) runtime = numbers[numbers.length - 1];
        }
        let originalTitle = "";
        const genresMatch = infoText.match(/类型\s*[::：]\s*([^\n]+)/);
        const countriesMatch = infoText.match(/制片国家\/地区\s*[::：]?\s*([^\n]+)/);
        const episodeCountMatch = infoText.match(/集数\s*[::：]\s*(\d{1,4})/);
        const aliasMatch = infoText.match(/又名\s*[::：]\s*([^\n]+)/);
        if (aliasMatch) {
            const latin = splitAliases(aliasMatch[1]).find(looksLatin);
            if (latin) originalTitle = latin;
        }
        const languagesMatch = infoText.match(/语言\s*[::：]\s*([^\n]+)/);
        // 导演/编剧/主演：对照填写需要人物信息；主演行可能极长，截断到 1200 字符内的人名
        const peopleMatch = (label, maxChars = 400) => {
            const match = infoText.match(new RegExp(`${label}\\s*[::：]\\s*([^\\n]+)`));
            return match ? splitAliases(match[1].slice(0, maxChars)) : [];
        };
        return {
            doubanId: String(doubanId || ""),
            title,
            year,
            date,
            overview,
            runtime,
            originalTitle,
            genres: genresMatch ? genresMatch[1].trim() : "",
            countries: countriesMatch ? countriesMatch[1].trim() : "",
            languages: languagesMatch ? languagesMatch[1].trim() : "",
            aliases: aliasMatch ? aliasMatch[1].trim() : "",
            rating: ratingNode ? String(ratingNode.textContent || "").trim() : "",
            episodeCount: episodeCountMatch ? Number(episodeCountMatch[1]) : 0,
            directors: peopleMatch("导演"),
            writers: peopleMatch("编剧"),
            cast: peopleMatch("主演", 1200),
            poster: upgradeDoubanPosterUrl(picNode ? String(picNode.getAttribute("src") || picNode.getAttribute("content") || "").trim() : ""),
            doubanUrl: `https://movie.douban.com/subject/${doubanId}/`
        };
    }

    // m.douban.com rexxar 接口的 JSON → 与 parseDoubanDetailHtml 相同的条目结构
    function parseDoubanRexxarJson(data, doubanId) {
        if (!data || typeof data !== "object") return null;
        const title = String(data.title || "").trim();
        if (!title) return null;
        const joinList = (value) => (Array.isArray(value) ? value.map((item) => {
            const name = typeof item === "object" && item !== null ? String(item.name || "").trim() : String(item || "").trim();
            return name;
        }).filter(Boolean).join("/") : "");
        let runtime = 0;
        const durations = Array.isArray(data.durations) ? data.durations : [];
        for (const item of durations) {
            const match = String(item || "").match(/(\d{1,4})\s*分钟/);
            if (match) runtime = Number(match[1]);
        }
        // pubdate 形如 "2026-08-15(中国大陆)"，取第一个能解析出完整日期的
        let date = "";
        const pubdates = Array.isArray(data.pubdate) ? data.pubdate : [];
        for (const item of pubdates) {
            date = normalizeAirDate(String(item || "").split("(")[0].split("（")[0]);
            if (date) break;
        }
        const aliases = Array.isArray(data.aka) ? data.aka.map((item) => String(item || "").trim()).filter(Boolean) : [];
        const originalTitle = String(data.original_title || "").trim() || aliases.find(looksLatin) || "";
        const ratingValue = data.rating && typeof data.rating === "object" ? Number(data.rating.value) : 0;
        const pic = data.pic && typeof data.pic === "object" ? data.pic : {};
        return {
            doubanId: String(doubanId || data.id || ""),
            title,
            year: parseYearValue(data.year),
            date,
            overview: collapseSummary(String(data.intro || "")),
            runtime,
            originalTitle,
            genres: joinList(data.genres),
            countries: joinList(data.countries),
            languages: joinList(data.languages),
            aliases: aliases.join("/"),
            rating: ratingValue > 0 ? ratingValue.toFixed(1) : "",
            episodeCount: Number(data.episodes_count) || 0,
            directors: joinList(data.directors),
            writers: joinList(data.writers),
            cast: joinList(data.actors),
            poster: upgradeDoubanPosterUrl(String(pic.large || pic.normal || data.image || data.cover_url || "").trim()),
            doubanUrl: `https://movie.douban.com/subject/${doubanId}/`
        };
    }

    async function fetchDoubanDetailRexxar(doubanId, config) {
        // 移动端 JSON 接口：无需登录，剧集 ID 请求 movie 时会 301 到 tv，交给请求层自动跟随
        const response = await tmdbhDoubanFetch(`https://m.douban.com/rexxar/api/v2/movie/${doubanId}`, config, TMDBH_REXXAR_HEADERS);
        let data = null;
        try {
            data = JSON.parse(response.responseText);
        } catch (err) {
            throw new Error("豆瓣移动端接口响应不是 JSON（可能被风控）");
        }
        return parseDoubanRexxarJson(data, doubanId);
    }

    // rexxar 接口字段不全是常态（编剧恒为 null、语言/又名等常缺），这些字段要靠桌面页补
    function doubanDetailHasGaps(detail) {
        const d = detail && typeof detail === "object" ? detail : {};
        return ["writers", "directors", "genres", "countries", "languages", "aliases", "overview"].some((key) => !String(d[key] || "").trim());
    }

    // 只把 fallback 里的非空字段填进 base 的空位，不覆盖已有值（rexxar 主路 + 桌面页补全）
    function mergeDoubanDetail(base, fallback) {
        const merged = Object.assign({}, base && typeof base === "object" ? base : {});
        const src = fallback && typeof fallback === "object" ? fallback : {};
        for (const key of Object.keys(src)) {
            const incoming = src[key];
            if (incoming === undefined || incoming === null || incoming === "" || incoming === 0 || (Array.isArray(incoming) && !incoming.length)) continue;
            const current = merged[key];
            if (current === undefined || current === null || current === "" || current === 0 || (Array.isArray(current) && !current.length)) {
                merged[key] = incoming;
            }
        }
        return merged;
    }

    async function fetchDoubanDetail(doubanId, config) {
        const id = String(doubanId || "").replace(/\D/g, "");
        if (!id) throw new Error("无效的豆瓣条目 ID");
        const cacheKey = `detail:${id}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        // 主路 rexxar 接口（未登录可用），兜底桌面版 HTML 页；两边都没拿到标题多半是被风控
        let detail = null;
        let lastError = null;
        try {
            detail = await fetchDoubanDetailRexxar(id, config);
        } catch (err) {
            lastError = err;
        }
        // rexxar 缺编剧/语言等字段时用桌面页补全（登录用户桌面页是完整 HTML；匿名请求是 JS 壳，解析不出标题则跳过）
        if (detail && doubanDetailHasGaps(detail)) {
            try {
                const html = await tmdbhDoubanGetText(`https://movie.douban.com/subject/${id}/`, config, (text) => text.includes("v:itemreviewed"));
                const htmlDetail = parseDoubanDetailHtml(html, id);
                if (String(htmlDetail.title || "").trim()) detail = mergeDoubanDetail(detail, htmlDetail);
            } catch (err) { /* 桌面页拿不到就只靠 rexxar 的字段 */ }
        }
        if (!detail) {
            try {
                const html = await tmdbhDoubanGetText(`https://movie.douban.com/subject/${id}/`, config, (text) => text.includes("v:itemreviewed"));
                detail = parseDoubanDetailHtml(html, id);
            } catch (err) {
                lastError = err;
            }
        }
        if (!detail || !String(detail.title || "").trim()) {
            const reason = lastError ? `：${lastError.message}` : "";
            throw new Error(`豆瓣条目读取失败${reason}。可稍后重试，或先在浏览器登录豆瓣再试`);
        }
        tmdbhDoubanCacheSet(cacheKey, detail);
        return detail;
    }

    async function fetchDoubanDetailByImdb(imdbId, config) {
        const found = await searchDoubanByImdb(imdbId, config);
        if (!found) throw new Error("豆瓣未找到该 IMDb 编号对应条目");
        return fetchDoubanDetail(found.id, config);
    }

    // src/baike.js —— 百度百科数据源（搜索 / 词条详情 / 分集剧情；解析函数为纯函数供测试切片）。
    // 百科页面结构多代并存（新版 React 渲染 + 旧版服务端渲染），解析全部做成多选择器兜底，
    // 命中不了时明确报错而不是静默给空数据。
    const TMDBH_BAIKE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://baike.baidu.com/"
    };
    let tmdbhBaikeLastRequestAt = 0;

    async function tmdbhBaikeThrottle() {
        const interval = 900;
        const elapsed = Date.now() - tmdbhBaikeLastRequestAt;
        if (elapsed < interval) await tmdbhSleep(interval - elapsed);
        tmdbhBaikeLastRequestAt = Date.now();
    }

    async function tmdbhBaikeGetText(url, config) {
        if (config && config.baike && config.baike.enabled === false) {
            throw new Error("百度百科功能已在设置中关闭");
        }
        await tmdbhBaikeThrottle();
        const response = await tmdbhGmRequest({ method: "GET", url, headers: TMDBH_BAIKE_HEADERS, timeout: 20000 });
        if (response.status >= 400) throw new Error(`百度百科返回 HTTP ${response.status}`);
        const text = String(response.responseText || "");
        // 百度风控：HTTP 200 但正文是安全验证页
        if (text.slice(0, 4000).includes("百度安全验证")) {
            throw new Error("百度百科要求安全验证：请在浏览器里打开一次 baike.baidu.com 完成验证后重试");
        }
        return text;
    }

    function tmdbhBaikeCleanText(text) {
        return String(text || "").replace(/\s+/g, " ").trim();
    }

    // 条目链接/输入 → { name, id }：接受完整 URL（含百分号编码）与站内 /item/ 名称/ID 路径两种形态
    function parseBaikeItemUrl(text) {
        const match = String(text || "").trim().match(/(?:baike\.baidu\.com)?\/item\/([^#?\s/]+)(?:\/(\d+))?/i);
        if (!match) return null;
        let name = match[1];
        try { name = decodeURIComponent(name); } catch (err) { /* 保持原样 */ }
        return { name, id: match[2] || "" };
    }

    // 信息卡键值对：经典结构 .basicInfo-item.name / .value 相邻交替；兜底任意 dt/dd 交替。
    // 键名去掉全部空白（百科的 dt 标签写作「类 型」「中 文 名」），值保留正常空格
    function parseBaikeInfoPairs(doc) {
        const pairs = [];
        const push = (rawName, rawValue) => {
            const name = tmdbhBaikeCleanText(rawName).replace(/\s+/g, "");
            const value = tmdbhBaikeCleanText(rawValue);
            if (name && value) pairs.push([name, value]);
        };
        doc.querySelectorAll(".basic-info .basicInfo-item.name, .basicInfo-item.name").forEach((nameEl) => {
            const valueEl = nameEl.nextElementSibling;
            if (!valueEl || !/(^|\s)value(\s|$)/.test(String(valueEl.className || ""))) return;
            push(nameEl.textContent, valueEl.textContent);
        });
        if (pairs.length) return pairs;
        doc.querySelectorAll("dt").forEach((dt) => {
            const dd = dt.nextElementSibling;
            if (!dd || dd.tagName !== "DD") return;
            push(dt.textContent, dd.textContent);
        });
        return pairs;
    }

    // 词条页 HTML → 百科详情（fields 与豆瓣详情结构对齐，normalizeBaikeDetail 再归一为统一记录）
    function parseBaikeDetailHtml(html, sourceUrl) {
        const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
        const metaContent = (selector, attr = "content") => {
            const node = doc.querySelector(selector);
            return node ? String(node.getAttribute(attr) || "").trim() : "";
        };
        const h1 = doc.querySelector("h1");
        const title = tmdbhBaikeCleanText(h1 && h1.textContent) || tmdbhBaikeCleanText(metaContent('meta[property="og:title"]'));
        if (!title) return null;
        const summaryNode = doc.querySelector(".lemma-summary, .J-lemma-summary");
        const overview = tmdbhBaikeCleanText(summaryNode && summaryNode.textContent) || tmdbhBaikeCleanText(metaContent('meta[property="og:description"]')) || tmdbhBaikeCleanText(metaContent('meta[name="description"]'));
        const posterNode = doc.querySelector("img.main-img, .main-img img, .summary-pic img, .contentPic img, .side-content img") || doc.querySelector('meta[property="og:image"]');
        const pairs = parseBaikeInfoPairs(doc);
        const infoValue = (...labels) => {
            for (const label of labels) {
                const exact = pairs.find((pair) => pair[0] === label);
                if (exact) return exact[1];
            }
            for (const label of labels) {
                const fuzzy = pairs.find((pair) => pair[0].includes(label));
                if (fuzzy) return fuzzy[1];
            }
            return "";
        };
        const joined = (...labels) => splitAliases(infoValue(...labels)).join("/");
        const episodeMatch = infoValue("集数").match(/(\d{1,4})/);
        const runtimeMatch = infoValue("每集长度", "单集片长", "片长").match(/(\d{1,3})\s*分钟/);
        let date = "";
        for (const rawDate of splitAliases(infoValue("首播时间", "播出时间", "首播", "上映时间", "上映日期", "播出日期"))) {
            date = normalizeAirDate(rawDate);
            if (date) break;
        }
        const year = parseYearValue(infoValue("首播时间", "播出时间", "上映时间", "年代")) || parseYearValue(date) || parseYearValue(title);
        const parsedUrl = parseBaikeItemUrl(sourceUrl || "");
        return {
            baikeId: (parsedUrl && parsedUrl.id) || "",
            baikeUrl: sourceUrl || (parsedUrl && parsedUrl.name ? `https://baike.baidu.com/item/${encodeURIComponent(parsedUrl.name)}` : ""),
            title,
            originalTitle: splitAliases(infoValue("外文名", "外文片名", "外文剧名", "英文名")).find(looksLatin) || "",
            year,
            date,
            runtime: runtimeMatch ? Number(runtimeMatch[1]) : 0,
            overview,
            genres: joined("类型", "题材"),
            countries: joined("制片地区", "国家/地区", "出品地区"),
            languages: joined("语言"),
            aliases: joined("又名", "别名"),
            directors: joined("导演", "总导演"),
            writers: joined("编剧"),
            cast: joined("主演", "主要配音"),
            companies: joined("出品公司", "制作公司", "出品方"),
            networks: joined("播出平台", "在线播放平台", "网络播放平台", "首播电视台", "播出电视台"),
            episodeCount: episodeMatch ? Number(episodeMatch[1]) : 0,
            poster: upgradeBaikePosterUrl(posterNode ? String(posterNode.getAttribute("src") || posterNode.getAttribute("content") || "") : "")
        };
    }

    // 搜索结果页 → 候选列表：取带词条 ID 的 /item/ 链接，标题所在容器文本作摘要
    function searchBaikeResults(html) {
        const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
        const results = [];
        const seen = new Set();
        doc.querySelectorAll('a[href*="/item/"]').forEach((anchor) => {
            const parsed = parseBaikeItemUrl(anchor.getAttribute("href") || "");
            if (!parsed || !parsed.id || !parsed.name) return;
            if (seen.has(parsed.id)) return;
            const title = tmdbhBaikeCleanText(anchor.textContent);
            if (!title) return;
            seen.add(parsed.id);
            const container = anchor.closest("li, dl, .result-list, div") || anchor.parentElement;
            const abstract = tmdbhBaikeCleanText(container && container.textContent || "");
            results.push(createUnifiedRecord({
                source: "baike",
                sourceId: parsed.id,
                url: `https://baike.baidu.com/item/${encodeURIComponent(parsed.name)}/${parsed.id}`,
                title,
                overview: abstract.length > title.length + 8 ? `${abstract.slice(0, 120)}${abstract.length > 120 ? "…" : ""}` : "",
                year: parseYearValue(abstract)
            }));
        });
        return results.slice(0, 10);
    }

    async function searchBaikeEntries(keyword, config) {
        const query = String(keyword || "").trim();
        if (!query) throw new Error("请输入搜索关键词");
        // 粘贴条目链接：不经过搜索页，直接载入该词条详情
        if (/baike\.baidu\.com\/item\//i.test(query)) {
            return [normalizeBaikeDetail(await fetchBaikeDetail(query, config))];
        }
        const cacheKey = `baike:search:${query}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        const html = await tmdbhBaikeGetText(`https://baike.baidu.com/search?word=${encodeURIComponent(query)}`, config);
        let results = searchBaikeResults(html);
        if (!results.length) {
            // 搜索页改版兜底：把关键词当词条名直连（多义词会落到消歧义页，可用链接精确指定）
            results = [createUnifiedRecord({
                source: "baike",
                sourceId: query,
                url: `https://baike.baidu.com/item/${encodeURIComponent(query)}`,
                title: query
            })];
        }
        tmdbhDoubanCacheSet(cacheKey, results);
        return results;
    }

    async function fetchBaikeDetail(idOrUrl, config) {
        const raw = String(idOrUrl || "").trim();
        if (!raw) throw new Error("无效的百科条目链接或词条名");
        let url = "";
        if (/^https?:\/\//i.test(raw)) {
            if (!/baike\.baidu\.com/i.test(raw)) throw new Error("只支持 baike.baidu.com 的词条链接");
            url = raw.split("#")[0];
        } else {
            const parsed = parseBaikeItemUrl(raw);
            const name = parsed && parsed.name ? parsed.name : raw;
            url = `https://baike.baidu.com/item/${encodeURIComponent(name)}${parsed && parsed.id ? `/${parsed.id}` : ""}`;
        }
        const cacheKey = `baike:detail:${url}`;
        const cached = tmdbhDoubanCacheGet(cacheKey);
        if (cached !== undefined) return cached;
        const html = await tmdbhBaikeGetText(url, config);
        const detail = parseBaikeDetailHtml(html, url);
        if (!detail || !String(detail.title || "").trim()) {
            throw new Error("百科条目解析失败（页面结构可能已变化，欢迎反馈）");
        }
        tmdbhDoubanCacheSet(cacheKey, detail);
        return detail;
    }

    // 分集剧情表的一行 → 分集行：第一格取集号（第N集/N/E N/第N期），日期格单独识别，
    // 余格里最长的是剧情、其余短文本是集名（集数｜集名｜剧情 三列与 集数｜剧情 两列都支持）
    function mapBaikeEpisodeCells(cells) {
        const list = (Array.isArray(cells) ? cells : []).map((cell) => tmdbhBaikeCleanText(cell)).filter(Boolean);
        if (!list.length) return null;
        const numberMatch = list[0].match(/(?:第\s*)?(\d{1,4})\s*(?:[集期话])?/);
        const number = numberMatch ? Number(numberMatch[1]) : 0;
        if (!number || number > 2000) return null;
        const episode = { episodeNumber: number, name: "", airDate: "", overview: "", runtime: 0, stillUrl: "" };
        const rest = list.slice(1);
        const dateIndex = rest.findIndex((cell) => normalizeAirDate(cell));
        if (dateIndex >= 0) {
            episode.airDate = normalizeAirDate(rest[dateIndex]);
            rest.splice(dateIndex, 1);
        }
        if (rest.length) {
            let longestIndex = 0;
            rest.forEach((cell, index) => { if (cell.length > rest[longestIndex].length) longestIndex = index; });
            const longest = rest[longestIndex];
            if (longest.length >= 15 || rest.length > 1) {
                episode.overview = longest;
                rest.splice(longestIndex, 1);
                if (rest.length) episode.name = rest[0];
            } else {
                episode.name = longest;
            }
        }
        return episode;
    }

    // 词条页 → 分集列表：「分集剧情」标题向后找表格；找不到再全页按表头特征兜底（集数/剧情）
    function parseBaikeEpisodes(html) {
        const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
        const isEpisodeTable = (table) => {
            const firstRow = table.querySelector("tr");
            const headText = tmdbhBaikeCleanText(firstRow && firstRow.textContent || "");
            return /集数|剧情|分集|集名/.test(headText) && table.querySelectorAll("tr").length >= 2;
        };
        const tableSet = new Set();
        const headings = Array.from(doc.querySelectorAll("h2, h3, h4, .para-title, .title-text")).filter((el) => /分集剧情|分集介绍|各集剧情|剧集介绍/.test(tmdbhBaikeCleanText(el.textContent)));
        for (const heading of headings) {
            let node = heading.nextElementSibling;
            for (let hop = 0; hop < 15 && node; hop++) {
                node.querySelectorAll("table").forEach((table) => tableSet.add(table));
                if (tableSet.size) break;
                if (/^(h1|h2|h3|h4)$/i.test(node.tagName || "")) break;
                node = node.nextElementSibling;
            }
        }
        if (!tableSet.size) {
            doc.querySelectorAll("table").forEach((table) => {
                if (isEpisodeTable(table)) tableSet.add(table);
            });
        }
        const episodes = [];
        const seen = new Set();
        for (const table of tableSet) {
            if (!isEpisodeTable(table)) continue;
            for (const row of table.querySelectorAll("tr")) {
                const cells = Array.from(row.querySelectorAll("td, th")).map((cell) => cell.textContent);
                const episode = mapBaikeEpisodeCells(cells);
                if (episode && !seen.has(episode.episodeNumber)) {
                    seen.add(episode.episodeNumber);
                    episodes.push(episode);
                }
            }
        }
        episodes.sort((a, b) => a.episodeNumber - b.episodeNumber);
        return { title: "", overview: "", cover: "", episodes };
    }

    async function fetchBaikeEpisodes(url, config) {
        const target = String(url || "").trim();
        if (!/baike\.baidu\.com\/item\//i.test(target)) throw new Error("没有可抓取的百科词条链接");
        const html = await tmdbhBaikeGetText(target, config);
        const result = parseBaikeEpisodes(html);
        if (!result.episodes.length) {
            throw new Error("该词条页没有解析到分集剧情表格（部分剧集的分集剧情在独立词条里，可搜索「剧名 分集剧情」后粘贴该词条链接重试）");
        }
        return result;
    }

    // src/tmdbapi.js —— TMDB 官方 API v3 客户端（只读；新增/编辑数据走官网表单与内部接口）
    const TMDBH_TMDB_API_BASE = "https://api.themoviedb.org/3";

    const TMDBH_GROUP_TYPES = { 1: "首播顺序", 2: "绝对顺序", 3: "DVD 顺序", 4: "数字/流媒体顺序", 5: "故事线", 6: "制作顺序" };

    function tmdbhTmdbReady(config) {
        return Boolean(config && config.tmdb && String(config.tmdb.apiKey || "").trim().length >= 8);
    }

    async function tmdbhTmdbGet(config, path, params) {
        const query = new URLSearchParams(Object.assign({ api_key: String(config.tmdb.apiKey || "").trim(), language: "zh-CN" }, params || {}));
        const response = await tmdbhGmRequest({ method: "GET", url: `${TMDBH_TMDB_API_BASE}${path}?${query.toString()}`, headers: { "Accept": "application/json" }, timeout: 20000 });
        if (response.status === 401) throw new Error("TMDB API Key 无效（HTTP 401），请到 themoviedb.org → 设置 → API 检查");
        if (response.status === 404) throw new Error("TMDB 未找到该条目（HTTP 404）");
        if (response.status >= 400) throw new Error(`TMDB API 返回 HTTP ${response.status}`);
        try {
            return JSON.parse(response.responseText);
        } catch (err) {
            throw new Error("TMDB API 响应不是 JSON");
        }
    }

    async function tmdbhTmdbFindImdb(config, imdbId) {
        const data = await tmdbhTmdbGet(config, `/find/${encodeURIComponent(imdbId)}`, { external_source: "imdb_id" });
        return {
            movies: Array.isArray(data.movie_results) ? data.movie_results : [],
            tv: Array.isArray(data.tv_results) ? data.tv_results : []
        };
    }

    async function tmdbhTmdbEpisodeGroups(config, tvId) {
        const data = await tmdbhTmdbGet(config, `/tv/${Number(tvId)}/episode_groups`);
        return Array.isArray(data.results) ? data.results : [];
    }

    async function tmdbhTmdbEpisodeGroupDetails(config, groupId) {
        return tmdbhTmdbGet(config, `/tv/episode_group/${encodeURIComponent(groupId)}`);
    }


    // src/sites.js —— 流媒体平台分集抓取器（平台链接 → 分集列表）。
    // 接口移植自 TMDB-Import 的免浏览器提取器：这些站点都有公开 JSON 接口，用 GM 跨域请求
    // 即可拿到，不需要 Playwright。映射器（map* / parse*）是纯函数，供测试切片。
    const TMDBH_SITE_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

    // Unix 秒 → 本地时区 YYYY-MM-DD
    function epochToLocalDate(seconds) {
        const date = new Date(Number(seconds) * 1000);
        if (Number.isNaN(date.getTime())) return "";
        return `${date.getFullYear()}-${tmdbhPad2(date.getMonth() + 1)}-${tmdbhPad2(date.getDate())}`;
    }

    // 腾讯/优酷接口的 JSONP 外壳：剥掉 QZOutputJson= 前缀与结尾分号
    function tmdbhSiteJsonp(text) {
        return String(text || "").replace(/^\s*QZOutputJson=/, "").replace(/;\s*$/, "");
    }

    // 腾讯标题清洗：「剧名_01」「第1集 标题」→「标题」
    function cleanQqTitle(title) {
        return String(title || "")
            .replace(/_\d+$/, "")
            .replace(/^第\d+[集期]\s*/, "")
            .trim();
    }

    // —— URL 解析（纯函数） ——
    // B站：/bangumi/play/ss123 /bangumi/play/ep456 /bangumi/media/md789
    function parseBilibiliUrl(url) {
        const match = String(url || "").match(/bilibili\.com\/bangumi\/(?:play\/(ss|ep)(\d+)|media\/md(\d+))/i);
        if (!match) return null;
        if (match[1]) return { type: match[1].toLowerCase(), id: match[2] };
        return { type: "md", id: match[3] };
    }

    // 爱奇艺：专辑页 HTML 里挖 albumId（三种页面形态各有一个埋点）；
    // 桌面版单集页（v_）是 JS 空壳没有数据，用移动端 m 站页面（SSR 带选集列表）兜底
    function extractIqiyiAlbumId(url, html) {
        const text = String(html || "");
        let match = null;
        if (/iqiyi\.com\/lib\/m_/i.test(url)) match = text.match(/movlibalbumaid="(\d+)"/i);
        else if (/iqiyi\.com\/a_/i.test(url)) match = text.match(/data-album-id="(\d+)"/i);
        else match = text.match(/"albumId":\s*"?(\d+)/) || text.match(/"albumId":(\d+),"channelId/);
        return match ? match[1] : "";
    }

    // 芒果TV：路径里第一个数字段是 collection_id（w.mgtv.com/b/419629/17004788 → 419629）
    function parseMgtvCollectionId(url) {
        const path = String(url || "").split(".html", 1)[0];
        const match = path.match(/(\d+)/);
        return match ? match[1] : "";
    }

    // 腾讯视频：/x/cover/{cid}…
    function parseQqCoverCid(url) {
        const match = String(url || "").match(/v\.qq\.com\/x\/cover\/([^/.]+)/i);
        return match ? match[1] : "";
    }

    // 优酷：?s=showId 优先；否则 id_XXX.html / vid= 里的视频 ID
    function parseYoukuTarget(url) {
        const text = String(url || "");
        const show = text.match(/[?&]s=([a-zA-Z0-9_-]+)/);
        if (show) return { type: "show", id: show[1] };
        const vidParam = text.match(/[?&]vid=([a-zA-Z0-9_-]+)/);
        const idPath = text.match(/id_([a-zA-Z0-9_=]+)\.html/);
        const videoId = (vidParam ? vidParam[1] : "") || (idPath ? idPath[1].replace(/=+$/, "") : "");
        return videoId ? { type: "video", id: videoId } : null;
    }

    // —— 分集映射（纯函数，输出与粘贴解析同构的表格行） ——
    function mapBilibiliEpisodes(result) {
        const eps = result && typeof result === "object" && Array.isArray(result.episodes) ? result.episodes : [];
        let seq = 0;
        const rows = [];
        for (const ep of eps) {
            if (ep.badge === "预告") continue; // 预告不算正片（TMDB-Import 同款规则）
            seq++;
            const title = String(ep.title || "");
            const name = (title.includes("（上") || title.includes("（下")) && ep.long_title
                ? `${title} ${ep.long_title}`.trim()
                : String(ep.long_title || ep.title || "").trim() || `第 ${seq} 集`;
            // 集号恒定顺序递增：提交目标是 TMDB 集号，预告剔除后必须连续（TMDB-Import 同款行为）
            rows.push({
                episodeNumber: seq,
                name,
                airDate: ep.pub_time ? epochToLocalDate(ep.pub_time) : normalizeAirDate(ep.release_date || ""),
                runtime: Math.round((Number(ep.duration) || 0) / 60000),
                overview: "",
                stillUrl: String(ep.cover || "")
            });
        }
        return rows;
    }

    // 爱奇艺封面升级到最大档：imageSize 数组里挑面积最大的 W_H，按 TMDB-Import 规则拼进 URL。
    // 实测裸 imageUrl 只有 120×160 移动小图，追加 _1248_702 后为 1248×702（平台最大 16:9 档）。
    function upgradeIqiyiImageUrl(url, sizes) {
        const base = String(url || "").trim();
        if (!base || !Array.isArray(sizes) || !sizes.length) return base;
        let best = "";
        let bestArea = 0;
        for (const item of sizes) {
            const match = String(item || "").match(/^(\d+)_(\d+)$/);
            if (!match) continue;
            const area = Number(match[1]) * Number(match[2]);
            if (area > bestArea) {
                bestArea = area;
                best = `${match[1]}_${match[2]}`;
            }
        }
        if (!best) return base;
        return `${base.replace(/\.[^.]+$/, "")}_${best}.jpg`;
    }

    function mapIqiyiEpisodes(epsodelist) {
        const eps = Array.isArray(epsodelist) ? epsodelist : [];
        return eps.map((ep) => {
            const parts = String(ep.duration || "").split(":");
            let runtime = 0;
            if (parts.length === 3) runtime = Number(parts[0]) * 60 + Number(parts[1]);
            else if (parts.length === 2) runtime = Number(parts[0]);
            return {
                episodeNumber: Number(ep.order) || 0,
                name: String(ep.subtitle || ep.name || "").trim(),
                airDate: normalizeAirDate(ep.period || ""),
                runtime,
                overview: String(ep.description || "").replace(/\s*\n\s*/g, " ").trim(),
                stillUrl: upgradeIqiyiImageUrl(ep.imageUrl, ep.imageSize)
            };
        }).filter((row) => row.episodeNumber > 0);
    }

    // 芒果图片床是阿里云 OSS：裸 URL 只有 860×484 预览档，带 resize 参数才返回 ≥1280 的原图（实测 1280×720）
    function appendMgtvOssResize(url) {
        const text = String(url || "").trim();
        if (!text || !/hitv\.com/i.test(text)) return text;
        return `${text}${text.includes("?") ? "&" : "?"}x-oss-process=image/resize,w_1280`;
    }

    function mapMgtvEpisodes(list) {
        const eps = Array.isArray(list) ? list : [];
        return eps.filter((ep) => String(ep.isIntact) === "1").map((ep) => ({
            episodeNumber: Number(ep.t1) || 0,
            name: String(ep.t2 || "").trim(),
            airDate: normalizeAirDate(String(ep.ts || "").split(" ")[0]),
            runtime: Number(String(ep.time || "").split(":")[0]) || 0,
            overview: "",
            stillUrl: appendMgtvOssResize(String(ep.img || "").replace(/_[^_]*$/, ""))
        })).filter((row) => row.episodeNumber > 0);
    }

    function mapQqUnionEpisodes(fieldsList) {
        const items = Array.isArray(fieldsList) ? fieldsList : [];
        let counter = 1;
        return items.filter((item) => {
            const category = item && item.category_map;
            return Array.isArray(category) && category.some((tag) => typeof tag === "string" && tag.includes("正片"));
        }).map((item) => {
            let number = parseInt(item.episode, 10) || 0;
            if (!number) number = counter;
            if (number >= counter) counter = number + 1;
            return {
                episodeNumber: number,
                name: cleanQqTitle(item.second_title || item.title || ""),
                airDate: normalizeAirDate(String(item.video_checkup_time || "").split(" ")[0]),
                runtime: Math.round((Number(item.duration) || 0) / 60),
                overview: String(item.desc || "").replace(/\s*\n\s*/g, " ").trim(),
                stillUrl: String(item.pic160x90 || "").replace("/160", "/1280")
            };
        }).sort((a, b) => a.episodeNumber - b.episodeNumber);
    }

    function mapYoukuVideos(videos) {
        const eps = Array.isArray(videos) ? videos : [];
        return eps.map((ep, index) => ({
            // seq 是平台给的正片集号；缺失时顺序回退
            episodeNumber: Number(ep.seq) || index + 1,
            name: String(ep.rc_title || ep.title || "").trim(),
            airDate: normalizeAirDate(String(ep.published || "").split(" ")[0]),
            runtime: Math.round((Number(ep.duration) || 0) / 60),
            overview: String(ep.description || "").replace(/\s*\n\s*/g, " ").trim(),
            stillUrl: String(ep.bigthumbnail || ep.bigThumbnail || ep.thumbnail || "")
        }));
    }

    // 红果短剧（hongguoduanju.com，番茄系 SSR 页）：页面内嵌 window._ROUTER_DATA。
    // 括号配对截出完整 JSON（字符串内的花括号/引号需跳过）
    function extractHongguoRouterData(html) {
        const text = String(html || "");
        const marker = text.indexOf("_ROUTER_DATA");
        if (marker < 0) throw new Error("页面里没有 _ROUTER_DATA（页面结构可能已变化）");
        const jsonStart = text.indexOf("{", marker);
        let depth = 0;
        let i = jsonStart;
        let inStr = false;
        let esc = false;
        while (i < text.length) {
            const ch = text[i];
            if (inStr) {
                if (esc) esc = false;
                else if (ch === "\\") esc = true;
                else if (ch === '"') inStr = false;
            } else if (ch === '"') inStr = true;
            else if (ch === "{") depth++;
            else if (ch === "}") {
                depth--;
                if (!depth) break;
            }
            i++;
        }
        return JSON.parse(text.slice(jsonStart, i + 1));
    }

    function parseHongguoSeries(html) {
        const data = extractHongguoRouterData(html);
        const detail = data && data.loaderData && data.loaderData.detail_page && data.loaderData.detail_page.seriesDetail;
        if (!detail) throw new Error("页面数据里没有剧集详情");
        const vids = Array.isArray(detail.vid_list) ? detail.vid_list : [];
        const total = Number(detail.episode_cnt) || vids.length;
        const episodes = vids.slice(0, 500).map((vid, index) => ({
            episodeNumber: index + 1,
            name: "",
            airDate: "",
            runtime: 0,
            overview: "",
            stillUrl: ""
        }));
        return {
            title: String(detail.series_name || "").trim(),
            overview: String(detail.series_intro || "").trim(),
            cover: String(detail.series_cover || ""),
            episodeCnt: total,
            episodes
        };
    }

    // 红果站内搜索（/search/{关键词}）：searchList 每项带 video_data（series_id/标题/集数/简介/封面）
    function mapHongguoSearchList(searchList) {
        const items = Array.isArray(searchList) ? searchList : [];
        return items.map((item) => (item && typeof item === "object" ? item : {}))
            .filter((item) => item.video_data && item.video_data.series_id)
            .map((item) => ({
                platform: "hongguo",
                platformName: "红果短剧",
                id: String(item.video_data.series_id),
                title: String(item.video_data.series_title || item.name || "").trim(),
                episodeCnt: Number(item.video_data.episode_cnt) || 0,
                intro: String(item.video_data.series_intro || "").trim(),
                cover: String(item.video_data.series_cover || "")
            }));
    }

    // B站番剧搜索（media_bangumi）：title 带 <em> 高亮标签要剥掉；eps 带分集列表
    function mapBilibiliSearchResults(data) {
        const result = data && data.data && Array.isArray(data.data.result) ? data.data.result : [];
        return result.map((item) => ({
            platform: "bilibili",
            platformName: "哔哩哔哩",
            id: String(Number(item.season_id) || 0),
            title: String(item.title || "").replace(/<[^>]+>/g, "").trim(),
            episodeCnt: Array.isArray(item.eps) ? item.eps.length : 0,
            intro: String(item.desc || "").replace(/<[^>]+>/g, "").trim(),
            cover: String(item.cover || "")
        })).filter((item) => item.id !== "0");
    }

    // —— 运行时抓取（GM 跨域请求） ——
    async function tmdbhSiteGetJSON(url, jsonp) {
        const response = await tmdbhGmRequest({ method: "GET", url, headers: { "User-Agent": TMDBH_SITE_UA, "Accept": "application/json,text/plain,*/*" }, timeout: 20000 });
        if (response.status >= 400) throw new Error(`接口返回 HTTP ${response.status}`);
        const text = jsonp ? tmdbhSiteJsonp(response.responseText) : response.responseText;
        try {
            return JSON.parse(text);
        } catch (err) {
            throw new Error("接口响应不是 JSON（可能被风控或需要登录）");
        }
    }

    async function tmdbhSiteGetText(url, userAgent) {
        const response = await tmdbhGmRequest({ method: "GET", url, headers: { "User-Agent": userAgent || TMDBH_SITE_UA, "Accept": "text/html,*/*" }, timeout: 25000 });
        if (response.status >= 400) throw new Error(`页面返回 HTTP ${response.status}`);
        return response.responseText;
    }

    // 爱奇艺移动端页只对手机 UA 返回 SSR 数据（桌面 UA 是 JS 空壳）
    const TMDBH_SITE_MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

    // B站搜索需要 buvid cookie：缺失时接口会返回风控页，先访问一次主站引导 cookie 再重试
    async function tmdbhBiliSearchJSON(keyword) {
        const url = `https://api.bilibili.com/x/web-interface/search/type?search_type=media_bangumi&keyword=${encodeURIComponent(keyword)}&page=1`;
        let data = null;
        try {
            data = await tmdbhSiteGetJSON(url);
        } catch (err) {
            data = null;
        }
        if (!data || data.code !== 0) {
            await tmdbhSiteGetText("https://www.bilibili.com/");
            data = await tmdbhSiteGetJSON(url);
        }
        if (!data || data.code !== 0) throw new Error(`B站搜索接口返回异常（code=${data && data.code}）`);
        return data;
    }

    const TMDBH_SITE_SOURCES = [
        {
            id: "bilibili",
            name: "哔哩哔哩",
            match: (url) => parseBilibiliUrl(url),
            async fetch(url) {
                const target = parseBilibiliUrl(url);
                let seasonId = target.id;
                if (target.type === "md") {
                    const media = await tmdbhSiteGetJSON(`https://api.bilibili.com/pgc/review/user?media_id=${target.id}`);
                    seasonId = media && media.result && media.result.media && media.result.media.season_id;
                    if (!seasonId) throw new Error("B站没找到该媒体对应的季");
                } else if (target.type === "ep") {
                    const byEp = await tmdbhSiteGetJSON(`https://api.bilibili.com/pgc/view/web/season?ep_id=${target.id}`);
                    if (!(byEp && byEp.code === 0 && byEp.result)) throw new Error("B站没找到该分集对应的季");
                    seasonId = byEp.result.season_id || target.id;
                }
                const data = await tmdbhSiteGetJSON(`https://api.bilibili.com/pgc/view/web/season?season_id=${seasonId}`);
                if (!(data && data.code === 0 && data.result)) throw new Error("B站接口没有返回该季数据");
                return { title: data.result.title || "", episodes: mapBilibiliEpisodes(data.result) };
            }
        },
        {
            id: "iqiyi",
            name: "爱奇艺",
            match: (url) => /iqiyi\.com\/(a_|v_|lib\/m_)/i.test(url),
            async fetch(url) {
                // 单集页（v_…）桌面版是 JS 空壳：改抓移动端 SSR 页（去掉查询参数），albumId 埋在选集数据里
                const pageUrl = /iqiyi\.com\/v_[a-z0-9]+\.html/i.test(url)
                    ? String(url).match(/https?:\/\/[^?]+/i)[0].replace(/\/\/www\.iqiyi\.com/i, "//m.iqiyi.com")
                    : url;
                const html = await tmdbhSiteGetText(pageUrl, TMDBH_SITE_MOBILE_UA);
                const albumId = extractIqiyiAlbumId(url, html);
                if (!albumId) throw new Error("爱奇艺页面里没找到专辑 ID（可能需要登录或页面已变化）");
                let title = "";
                let overview = "";
                let cover = "";
                try {
                    const base = await tmdbhSiteGetJSON(`https://pcw-api.iqiyi.com/album/album/baseinfo/${albumId}`);
                    title = (base && base.data && base.data.name) || "";
                    overview = (base && base.data && base.data.description) || "";
                    cover = (base && base.data && base.data.imageUrl) || "";
                } catch (err) { /* 专辑信息拿不到不影响分集 */ }
                const episodes = [];
                for (let page = 1; page <= 20; page++) {
                    const data = await tmdbhSiteGetJSON(`https://pcw-api.iqiyi.com/albums/album/avlistinfo?aid=${albumId}&page=${page}&size=100`);
                    const list = data && data.data && data.data.epsodelist;
                    if (!Array.isArray(list) || !list.length) break;
                    episodes.push(...mapIqiyiEpisodes(list));
                    if (list.length < 100) break;
                }
                return { title, overview, cover, episodes };
            }
        },
        {
            id: "mgtv",
            name: "芒果TV",
            match: (url) => /mgtv\.com/i.test(url),
            async fetch(url) {
                const collectionId = parseMgtvCollectionId(url);
                if (!collectionId) throw new Error("芒果TV链接里没找到专辑 ID");
                const episodes = [];
                let title = "";
                for (let page = 1; page <= 20; page++) {
                    const data = await tmdbhSiteGetJSON(`https://pcweb.api.mgtv.com/episode/list?_support=10000000&version=5.5.35&collection_id=${collectionId}&page=${page}&size=50`);
                    const payload = data && data.data;
                    if (!payload) throw new Error("芒果TV接口没有返回数据");
                    episodes.push(...mapMgtvEpisodes(payload.list));
                    if (!payload.total_page || Number(payload.current_page) >= Number(payload.total_page)) break;
                }
                return { title, episodes };
            }
        },
        {
            id: "qq",
            name: "腾讯视频",
            match: (url) => parseQqCoverCid(url),
            async fetch(url) {
                const cid = parseQqCoverCid(url);
                const index = await tmdbhSiteGetJSON(`https://data.video.qq.com/fcgi-bin/data?otype=json&tid=431&idlist=${encodeURIComponent(cid)}&appid=10001005&appkey=0d1a9ddd94de871b`, true);
                const videoIds = index && index.results && index.results[0] && index.results[0].fields && index.results[0].fields.video_ids;
                if (!Array.isArray(videoIds) || !videoIds.length) throw new Error("腾讯视频没返回分集列表");
                const rows = [];
                for (let start = 0; start < videoIds.length; start += 30) {
                    const idlist = videoIds.slice(start, start + 30).join(",");
                    const data = await tmdbhSiteGetJSON(`https://union.video.qq.com/fcgi-bin/data?otype=json&tid=682&appid=20001238&appkey=6c03bbe9658448a4&idlist=${idlist}`, true);
                    if (data && Array.isArray(data.results)) rows.push(...mapQqUnionEpisodes(data.results.map((item) => item.fields)));
                }
                return { title: "", episodes: rows };
            }
        },
        {
            id: "hongguo",
            name: "红果短剧",
            match: (url) => /hongguoduanju\.com/i.test(url),
            async fetch(url) {
                const match = String(url).match(/player\/(\d+)/) || String(url).match(/series_id=(\d+)/);
                if (!match) throw new Error("红果链接里没找到 series_id（需要 /player/767… 或 ?series_id=767… 形式）");
                const html = await tmdbhSiteGetText(`https://hongguoduanju.com/detail?series_id=${match[1]}`);
                const result = parseHongguoSeries(html);
                if (!result.episodes.length) throw new Error(`红果没返回《${result.title || "该剧"}》的分集列表`);
                return { title: result.title, episodes: result.episodes };
            }
        },
        {
            id: "youku",
            name: "优酷",
            match: (url) => parseYoukuTarget(url),
            async fetch(url) {
                let target = parseYoukuTarget(url);
                let showId = target.type === "show" ? target.id : "";
                if (!showId) {
                    const video = await tmdbhSiteGetJSON(`https://openapi.youku.com/v2/videos/show.json?video_id=${target.id}&ext=show&client_id=0dec1b5a3cb570c1&package=com.huawei.hwvplayer.youku`);
                    if (video && video.error) throw new Error("优酷没找到该视频（链接可能已失效）");
                    showId = (video && video.show && video.show.id) || target.id;
                }
                const episodes = [];
                for (let page = 1; page <= 20; page++) {
                    const data = await tmdbhSiteGetJSON(`https://openapi.youku.com/v2/shows/videos.json?show_id=${showId}&show_videotype=${encodeURIComponent("正片")}&page=${page}&count=30&client_id=0dec1b5a3cb570c1&package=com.huawei.hwvplayer.youku`);
                    if (data && data.error) throw new Error("优酷开放接口返回错误（接口老旧，可能已停用）");
                    const list = data && Array.isArray(data.videos) ? data.videos : [];
                    episodes.push(...mapYoukuVideos(list));
                    if (!list.length || !data.total || page * 30 >= Number(data.total)) break;
                }
                return { title: "", episodes };
            }
        }
    ];

    function matchSiteSource(url) {
        const text = String(url || "").trim();
        if (!/^https?:\/\//i.test(text)) return null;
        return TMDBH_SITE_SOURCES.find((site) => {
            try {
                return Boolean(site.match(text));
            } catch (err) {
                return false;
            }
        }) || null;
    }

    // 图片床防盗链：按图片域名补 Referer（缺了会 403 或拿到降级小图）
    function tmdbhImageReferer(url) {
        const text = String(url || "");
        if (/hdslb\.com/i.test(text)) return "https://www.bilibili.com/";
        if (/iqiyipic\.com|iqiyi\.com/i.test(text)) return "https://www.iqiyi.com/";
        if (/qpic\.cn/i.test(text)) return "https://v.qq.com/";
        if (/hitv\.com/i.test(text)) return "https://www.mgtv.com/";
        if (/ykimg\.com/i.test(text)) return "https://v.youku.com/";
        if (/doubanio\.com|douban\.com/i.test(text)) return "https://movie.douban.com/";
        if (/bkimg\.cdn\.bcebos\.com|bkimg/i.test(text)) return "https://baike.baidu.com/";
        return "";
    }

    // src/dom.js —— 运行时 DOM 辅助（v1.2.1 精简后仅剩：豆瓣图片水合、编辑器语言识别）
    const tmdbhDoubanImgCache = new Map();
    function tmdbhDoubanImageObjectUrl(url) {
        if (tmdbhDoubanImgCache.has(url)) return tmdbhDoubanImgCache.get(url);
        const promise = tmdbhGmRequest({
            method: "GET",
            url,
            headers: { "User-Agent": TMDBH_DOUBAN_HEADERS["User-Agent"], "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8", "Referer": tmdbhImageReferer(url) || "https://movie.douban.com/" },
            responseType: "blob",
            timeout: 20000
        }).then((response) => {
            const blob = response.response;
            if (!blob || typeof blob !== "object") throw new Error("图片下载结果为空");
            return URL.createObjectURL(blob);
        }).catch(() => "");
        tmdbhDoubanImgCache.set(url, promise);
        return promise;
    }

    // 面板渲染后调用：把豆瓣/百科直链图片替换成 blob 本地副本，失败则隐藏破图
    function tmdbhHydrateDoubanImages(scopeEl) {
        try {
            (scopeEl || document).querySelectorAll('img[data-tmdbh-img="1"]').forEach((img) => {
                const src = img.getAttribute("src") || "";
                if (!/doubanio\.com|douban\.com\/view|bkimg\.cdn\.bcebos\.com/i.test(src)) return;
                tmdbhDoubanImageObjectUrl(src).then((objectUrl) => {
                    if (objectUrl) img.src = objectUrl;
                    else img.style.visibility = "hidden";
                });
            });
        } catch (err) { /* 水合失败不影响面板 */ }
    }

    function tmdbhDetectEditorLanguage() {
        const nodes = document.querySelectorAll(".k-input-value-text");
        for (const node of nodes) {
            const language = detectEditorLanguage(node.textContent || "");
            if (language) return language;
        }
        const fromUrl = new URLSearchParams(location.search).get("language");
        return fromUrl || "zh-CN";
    }

    // src/ui.js —— 搜索浮层（左键悬浮球开关、右键复制 tmdbid 标记；运行时 Shadow DOM）
    function tmdbhEsc(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
        ));
    }

    // 确认对话框：jsdom 等环境没有 confirm 实现（调用会抛错），此时默认放行（保持既有测试行为）
    function tmdbhConfirm(message) {
        try {
            if (typeof window.confirm !== "function") return true;
            return Boolean(window.confirm(message));
        } catch (err) {
            return true;
        }
    }

    function tmdbhEscAttr(value) {
        return tmdbhEsc(value).replace(/\n/g, "&#10;");
    }

    function tmdbhTruncate(text, max) {
        const s = String(text || "");
        return s.length > max ? `${s.slice(0, max - 1)}…` : s;
    }

    function tmdbhSession(key, value) {
        try {
            if (value === undefined) return sessionStorage.getItem(key);
            if (value === null) sessionStorage.removeItem(key);
            else sessionStorage.setItem(key, value);
        } catch (err) {
            /* 无痕模式等场景忽略 */
        }
        return undefined;
    }

    // 统一复制出口：优先异步 Clipboard API，失败回退隐藏 textarea + execCommand（http 页面/旧浏览器）
    async function tmdbhCopyText(text) {
        const value = String(text ?? "");
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(value);
                return;
            }
        } catch (err) { /* 权限被拒时走下面的回退 */ }
        const textarea = document.createElement("textarea");
        textarea.value = value;
        textarea.style.cssText = "position:fixed;top:-999px;left:-999px;opacity:0;";
        (document.body || document.documentElement).appendChild(textarea);
        textarea.select();
        try {
            const ok = document.execCommand && document.execCommand("copy");
            if (!ok) throw new Error("浏览器未能复制");
        } finally {
            textarea.remove();
        }
    }

    const TMDBH_UI_STYLES = `
        :host { all: initial; }
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        /* ============================================================
           TMDB 助手 · 鸿蒙光感玻璃（HarmonyOS Glass）
           交互形态：右侧停靠面板（非居中模态）。磨砂透光玻璃、蓝紫渐变强调、
           环境光晕、高光发丝描边；深/浅双主题跟随系统，面板宽度可拖拽。
           全部 .tmdbh-* 类名与 data 契约保持，供测试与既有逻辑复用。
           ============================================================ */
        .tmdbh-root {
            --tmdbh-accent: #4f7cff;
            --tmdbh-accent-2: #9b6dff;
            --tmdbh-accent-3: #38bdf8;
            --tmdbh-accent-ink: #ffffff;
            --tmdbh-accent-soft: rgba(99, 118, 255, .14);
            --tmdbh-accent-glow: rgba(99, 118, 255, .32);
            --tmdbh-deep: #eef1f7;
            --tmdbh-glass: rgba(250, 251, 253, .62);
            --tmdbh-glass-strong: rgba(255, 255, 255, .86);
            --tmdbh-glass-soft: rgba(255, 255, 255, .5);
            --tmdbh-field-bg: rgba(255, 255, 255, .55);
            --tmdbh-card-bg: rgba(255, 255, 255, .5);
            --tmdbh-seg-pill: rgba(255, 255, 255, .78);
            --tmdbh-text: #1a1e27;
            --tmdbh-muted: #687180;
            --tmdbh-border: rgba(255, 255, 255, .9);
            --tmdbh-border-soft: rgba(28, 34, 48, .1);
            --tmdbh-highlight: rgba(255, 255, 255, .8);
            --tmdbh-ok: #149a4f;
            --tmdbh-err: #d92d20;
            --tmdbh-warn-ink: #b45309;
            --tmdbh-radius: 18px;
            --tmdbh-shadow: 0 32px 80px -18px rgba(35, 45, 85, .3), 0 10px 28px -12px rgba(35, 45, 85, .18);
            --tmdbh-shadow-sm: 0 6px 20px -6px rgba(35, 45, 85, .22);
            --tmdbh-font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "HarmonyOS Sans SC", "Helvetica Neue", "Microsoft YaHei", system-ui, sans-serif;
        }
        .tmdbh-root[data-theme="dark"] {
            --tmdbh-accent: #6d9bff;
            --tmdbh-accent-2: #a78bfa;
            --tmdbh-accent-3: #22d3ee;
            --tmdbh-accent-ink: #ffffff;
            --tmdbh-accent-soft: rgba(120, 150, 255, .18);
            --tmdbh-accent-glow: rgba(120, 150, 255, .34);
            --tmdbh-deep: #10131c;
            --tmdbh-glass: rgba(18, 20, 28, .6);
            --tmdbh-glass-strong: rgba(30, 33, 44, .82);
            --tmdbh-glass-soft: rgba(255, 255, 255, .06);
            --tmdbh-field-bg: rgba(255, 255, 255, .07);
            --tmdbh-card-bg: rgba(255, 255, 255, .055);
            --tmdbh-seg-pill: rgba(255, 255, 255, .1);
            --tmdbh-text: #f0f2f8;
            --tmdbh-muted: #9aa3b4;
            --tmdbh-border: rgba(255, 255, 255, .16);
            --tmdbh-border-soft: rgba(255, 255, 255, .09);
            --tmdbh-highlight: rgba(255, 255, 255, .12);
            --tmdbh-ok: #4ade80;
            --tmdbh-err: #f87171;
            --tmdbh-warn-ink: #fbbf24;
            --tmdbh-shadow: 0 32px 90px -16px rgba(0, 0, 0, .7), 0 10px 30px -12px rgba(0, 0, 0, .5);
            --tmdbh-shadow-sm: 0 6px 22px -6px rgba(0, 0, 0, .55);
        }
        /* —— 悬浮球：玻璃光珠（左键开面板 / 右键复制 / 可拖拽 / Alt+T 隐藏） —— */
        .tmdbh-ball {
            position: fixed; z-index: 2147483647; width: 54px; height: 54px; border-radius: 50%;
            background:
                radial-gradient(circle at 30% 22%, rgba(255, 255, 255, .92), rgba(255, 255, 255, 0) 46%),
                radial-gradient(circle at 68% 80%, var(--tmdbh-accent-glow), rgba(0, 0, 0, 0) 55%),
                linear-gradient(150deg, rgba(255, 255, 255, .66), rgba(255, 255, 255, .16));
            backdrop-filter: blur(18px) saturate(180%); -webkit-backdrop-filter: blur(18px) saturate(180%);
            color: var(--tmdbh-accent); display: flex; align-items: center; justify-content: center;
            font: 800 10px/1.15 var(--tmdbh-font); letter-spacing: .2px;
            cursor: grab; user-select: none; text-align: center;
            border: 1px solid rgba(255, 255, 255, .92);
            box-shadow: 0 10px 30px rgba(31, 41, 80, .28), inset 0 1px 0 rgba(255, 255, 255, .95), 0 0 0 6px var(--tmdbh-accent-soft);
            transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
        }
        .tmdbh-root[data-theme="dark"] .tmdbh-ball {
            border-color: rgba(255, 255, 255, .22);
            background:
                radial-gradient(circle at 30% 22%, rgba(255, 255, 255, .28), rgba(255, 255, 255, 0) 46%),
                radial-gradient(circle at 68% 80%, var(--tmdbh-accent-glow), rgba(0, 0, 0, 0) 55%),
                linear-gradient(150deg, rgba(70, 76, 96, .8), rgba(22, 25, 34, .85));
            box-shadow: 0 10px 30px rgba(0, 0, 0, .5), inset 0 1px 0 rgba(255, 255, 255, .18), 0 0 0 6px var(--tmdbh-accent-soft);
        }
        .tmdbh-ball:hover { transform: scale(1.06); box-shadow: 0 14px 36px rgba(31, 41, 80, .34), inset 0 1px 0 rgba(255, 255, 255, .95), 0 0 0 10px var(--tmdbh-accent-soft); }
        .tmdbh-root[data-theme="dark"] .tmdbh-ball:hover { box-shadow: 0 14px 36px rgba(0, 0, 0, .6), inset 0 1px 0 rgba(255, 255, 255, .22), 0 0 0 10px var(--tmdbh-accent-soft); }
        .tmdbh-ball:active { cursor: grabbing; transform: scale(.97); }
        /* —— 停靠层：全屏透明容器，不拦截页面交互（面板自身可点） —— */
        .tmdbh-overlay {
            position: fixed; inset: 0; z-index: 2147483646;
            pointer-events: none;
        }
        /* —— 右侧停靠面板：玻璃磨砂 + 环境光晕 + 左缘渐变光带 —— */
        .tmdbh-window {
            position: absolute; right: 0; top: 0; bottom: 0;
            width: min(620px, 46vw); min-width: 420px;
            display: flex; flex-direction: column;
            border-radius: 26px 0 0 26px; overflow: hidden;
            border-left: 1px solid var(--tmdbh-border-soft);
            box-shadow: var(--tmdbh-shadow);
            font: 13px/1.55 var(--tmdbh-font); color: var(--tmdbh-text);
            -webkit-font-smoothing: antialiased;
            pointer-events: auto;
            transform: translateX(calc(100% + 60px));
            transition: transform .34s cubic-bezier(.32, .72, .28, 1);
            will-change: transform;
        }
        .tmdbh-overlay.open .tmdbh-window { transform: translateX(0); }
        /* 批量单集视图需要更宽的工作台 */
        .tmdbh-root[data-wide="1"] .tmdbh-window { width: min(940px, 66vw); }
        /* 磨砂玻璃层 + 顶部/角落环境光晕（沉浸光感） */
        .tmdbh-window::before {
            content: ""; position: absolute; inset: 0; z-index: -1;
            background:
                radial-gradient(90% 58% at 100% -12%, var(--tmdbh-accent-glow), rgba(0, 0, 0, 0) 56%),
                radial-gradient(72% 46% at 0% 104%, rgba(56, 189, 248, .16), rgba(0, 0, 0, 0) 60%),
                var(--tmdbh-glass);
            backdrop-filter: blur(44px) saturate(190%); -webkit-backdrop-filter: blur(44px) saturate(190%);
        }
        /* 左缘渐变光带（蓝→紫→青），面板与页面的视觉分界 */
        .tmdbh-window::after {
            content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; z-index: 4;
            background: linear-gradient(180deg, var(--tmdbh-accent-3), var(--tmdbh-accent), var(--tmdbh-accent-2));
            opacity: .9;
        }
        /* 宽度拖拽抓手 */
        .tmdbh-resizer {
            position: absolute; left: -3px; top: 0; bottom: 0; width: 10px;
            cursor: ew-resize; z-index: 5; touch-action: none;
        }
        .tmdbh-resizer::after {
            content: ""; position: absolute; left: 4px; top: 50%; width: 2px; height: 44px;
            transform: translateY(-50%); border-radius: 2px;
            background: linear-gradient(180deg, var(--tmdbh-accent-3), var(--tmdbh-accent-2));
            opacity: 0; transition: opacity .2s ease;
        }
        .tmdbh-window:hover .tmdbh-resizer::after { opacity: .7; }
        .tmdbh-resizer:hover::after { opacity: 1; }
        /* —— 面板头部 —— */
        .tmdbh-winhead {
            display: flex; align-items: center; gap: 12px;
            padding: 16px 18px 10px; user-select: none; flex: none;
        }
        .tmdbh-logo {
            width: 38px; height: 38px; border-radius: 12px; flex: none;
            background: linear-gradient(135deg, var(--tmdbh-accent), var(--tmdbh-accent-2));
            color: var(--tmdbh-accent-ink); display: flex; align-items: center; justify-content: center;
            font: 800 16px/1 var(--tmdbh-font);
            box-shadow: 0 8px 20px var(--tmdbh-accent-glow), inset 0 1px 0 rgba(255, 255, 255, .5);
        }
        .tmdbh-wintitle { min-width: 0; }
        .tmdbh-wintitle h2 { margin: 0; font-size: 16px; font-weight: 800; letter-spacing: -.02em; display: flex; align-items: center; gap: 8px; white-space: nowrap; }
        .tmdbh-ver { font-size: 10px; font-weight: 600; color: var(--tmdbh-muted); background: var(--tmdbh-glass-soft); border: .5px solid var(--tmdbh-border-soft); border-radius: 999px; padding: 1px 8px; }
        .tmdbh-wintitle p { margin: 3px 0 0; color: var(--tmdbh-muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .tmdbh-head-tools { margin-left: auto; display: flex; gap: 6px; flex: none; }
        .tmdbh-iconbtn {
            width: 30px; height: 30px; border-radius: 10px;
            border: 1px solid var(--tmdbh-border-soft); background: var(--tmdbh-glass-soft);
            color: var(--tmdbh-muted); font-size: 14px; cursor: pointer;
            transition: all .18s ease;
        }
        .tmdbh-iconbtn:hover { color: var(--tmdbh-text); border-color: var(--tmdbh-accent); box-shadow: 0 0 0 3px var(--tmdbh-accent-soft); }
        /* —— 视图页签：玻璃分段控件，选中项渐变药丸 —— */
        .tmdbh-viewtabs {
            display: flex; gap: 4px; flex: none;
            margin: 0 18px 12px; padding: 4px; overflow-x: auto;
            background: var(--tmdbh-glass-soft); border: 1px solid var(--tmdbh-border-soft); border-radius: 14px;
        }
        .tmdbh-viewtabs button {
            border: none; background: transparent; flex: 1; white-space: nowrap;
            padding: 7px 14px; border-radius: 10px; cursor: pointer;
            color: var(--tmdbh-muted); font: inherit; font-weight: 600; font-size: 12.5px;
            transition: all .2s ease;
        }
        .tmdbh-viewtabs button:hover { color: var(--tmdbh-text); }
        .tmdbh-viewtabs button.active {
            background: linear-gradient(135deg, var(--tmdbh-accent), var(--tmdbh-accent-2));
            color: var(--tmdbh-accent-ink); font-weight: 700;
            box-shadow: 0 6px 16px var(--tmdbh-accent-glow), inset 0 1px 0 rgba(255, 255, 255, .35);
        }
        .tmdbh-winbody { flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
        /* 批量单集：左栏设置 + 右栏表格 */
        .tmdbh-layout { flex: 1; min-height: 0; display: grid; grid-template-columns: 272px minmax(0, 1fr); }
        .tmdbh-overview {
            min-height: 0; display: flex; flex-direction: column; overflow: hidden;
            background: var(--tmdbh-glass-soft); border-right: 1px solid var(--tmdbh-border-soft);
        }
        .tmdbh-ov-summary {
            min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 10px;
            padding: 13px 16px; border-bottom: 1px solid var(--tmdbh-border-soft); flex: none;
        }
        .tmdbh-ov-summary h3 { margin: 0; font-size: 13px; font-weight: 700; letter-spacing: -.01em; }
        .tmdbh-ov-summary p { margin: 3px 0 0; color: var(--tmdbh-muted); font-size: 10px; max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .tmdbh-ov-status { display: flex; align-items: center; gap: 5px; color: var(--tmdbh-accent); font-size: 11px; white-space: nowrap; font-weight: 600; }
        .tmdbh-fcard-list { flex: 1; min-height: 0; overflow: auto; display: grid; grid-template-columns: 1fr; align-content: start; gap: 3px; padding: 9px; transform: translateZ(0); }
        .tmdbh-fcard {
            position: relative; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) 18px;
            align-items: center; gap: 8px; padding: 8px 10px; text-align: left;
            background: transparent; border: 1px solid transparent; border-radius: 10px; cursor: pointer;
            font: inherit; color: inherit; transition: background .15s ease, border-color .15s ease;
        }
        .tmdbh-fcard:hover { border-color: color-mix(in srgb, var(--tmdbh-accent) 45%, transparent); background: var(--tmdbh-accent-soft); }
        .tmdbh-fcard-copy { min-width: 0; display: grid; gap: 1px; }
        .tmdbh-fcard-copy strong { font-size: 11.5px; font-weight: 600; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; display: flex; align-items: center; gap: 5px; }
        .tmdbh-fcard-copy small { color: var(--tmdbh-muted); font-size: 10px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .tmdbh-fcard .tmdbh-fdot { width: 8px; height: 8px; border-radius: 50%; justify-self: end; }
        .tmdbh-fcard.locked .tmdbh-fdot { background: var(--tmdbh-muted); opacity: .55; }
        .tmdbh-fcard.filled .tmdbh-fdot { background: var(--tmdbh-ok); }
        .tmdbh-fcard.empty .tmdbh-fdot { background: var(--tmdbh-accent); opacity: .8; }
        .tmdbh-fcard.locked { opacity: .78; }
        .tmdbh-ov-foot { padding: 8px 10px; border-top: 1px solid var(--tmdbh-border-soft); display: flex; gap: 6px; flex: none; }
        .tmdbh-ov-foot .tmdbh-hint { flex: 1; align-self: center; }
        /* —— 内容区 —— */
        .tmdbh-detail { min-width: 0; min-height: 0; padding: 0 20px 22px; overflow: auto; transform: translateZ(0); }
        .tmdbh-pane { flex: 1; min-height: 0; overflow: auto; padding: 6px 20px 20px; transform: translateZ(0); }
        .tmdbh-section { padding: 15px 0 5px; border-top: 1px solid var(--tmdbh-border-soft); }
        .tmdbh-section:first-child { border-top: none; }
        .tmdbh-sec-title { min-height: 30px; display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 9px; }
        .tmdbh-sec-title h4 { margin: 0; font-size: 13px; font-weight: 700; letter-spacing: -.01em; }
        .tmdbh-sec-title > span { color: var(--tmdbh-muted); font-size: 11px; }
        .tmdbh-chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .tmdbh-chip { padding: 2px 10px; border-radius: 999px; font-size: 10.5px; font-weight: 600; background: var(--tmdbh-glass-soft); color: var(--tmdbh-muted); border: .5px solid var(--tmdbh-border-soft); }
        .tmdbh-chip.accent { color: var(--tmdbh-accent); background: var(--tmdbh-accent-soft); border-color: transparent; }
        .tmdbh-chip.ok { color: var(--tmdbh-ok); background: rgba(20, 154, 79, .12); border-color: transparent; }
        .tmdbh-root[data-theme="dark"] .tmdbh-chip.ok { background: rgba(74, 222, 128, .14); }
        .tmdbh-chip.err { color: var(--tmdbh-err); background: rgba(217, 45, 32, .1); border-color: transparent; }
        .tmdbh-root[data-theme="dark"] .tmdbh-chip.err { background: rgba(248, 113, 113, .14); }
        .tmdbh-chip.warn { color: var(--tmdbh-warn-ink); background: rgba(180, 83, 9, .12); border-color: transparent; }
        .tmdbh-root[data-theme="dark"] .tmdbh-chip.warn { background: rgba(251, 191, 36, .14); }
        /* 媒体条目头：海报 + 标题（内容区视觉锚点） */
        .tmdbh-media-header { display: grid; grid-template-columns: 128px minmax(0, 1fr); gap: 18px; padding: 6px 0 14px; }
        .tmdbh-media-poster { width: 128px; aspect-ratio: 2 / 3; overflow: hidden; border-radius: 14px; background: var(--tmdbh-glass-soft); box-shadow: 0 12px 30px rgba(35, 45, 85, .25), inset 0 0 0 .5px var(--tmdbh-border-soft); }
        .tmdbh-root[data-theme="dark"] .tmdbh-media-poster { box-shadow: 0 12px 30px rgba(0, 0, 0, .5), inset 0 0 0 .5px var(--tmdbh-border-soft); }
        .tmdbh-media-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .tmdbh-media-copy { min-width: 0; }
        .tmdbh-media-copy h3 { margin: 0; font-size: 20px; line-height: 1.28; font-weight: 800; letter-spacing: -.025em; }
        .tmdbh-media-copy > p { margin: 5px 0 0; color: var(--tmdbh-muted); font-size: 12.5px; }
        .tmdbh-media-overview { margin: 8px 0 0; color: var(--tmdbh-muted); font-size: 12.5px; max-width: 720px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
        .tmdbh-people { margin: 7px 0 0; color: var(--tmdbh-muted); font-size: 11.5px; line-height: 1.7; word-break: break-all; }
        .tmdbh-people b { color: var(--tmdbh-text); font-weight: 600; }
        /* 数据源页签 + 候选 */
        .tmdbh-srcbar {
            display: inline-flex; gap: 3px; align-items: center; flex-wrap: wrap;
            width: max-content; max-width: 100%; margin: 12px 0 2px; padding: 3px;
            background: var(--tmdbh-glass-soft); border: 1px solid var(--tmdbh-border-soft); border-radius: 12px;
        }
        .tmdbh-srcbar button {
            border: none; background: transparent; color: var(--tmdbh-muted);
            border-radius: 9px; padding: 6px 14px; cursor: pointer; font: inherit; font-weight: 600; font-size: 12px;
            transition: all .18s ease;
        }
        .tmdbh-srcbar button:hover { color: var(--tmdbh-text); }
        .tmdbh-srcbar button.active {
            background: linear-gradient(135deg, var(--tmdbh-accent), var(--tmdbh-accent-2));
            color: var(--tmdbh-accent-ink); font-weight: 700;
            box-shadow: 0 4px 12px var(--tmdbh-accent-glow), inset 0 1px 0 rgba(255, 255, 255, .35);
        }
        .tmdbh-candidate-rail { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(250px, 280px); gap: 10px; margin-top: 11px; overflow-x: auto; padding: 2px 2px 10px; }
        .tmdbh-candidate {
            display: grid; grid-template-columns: 76px minmax(0, 1fr) auto; gap: 12px; padding: 12px; text-align: left;
            background: var(--tmdbh-card-bg); border: 1px solid var(--tmdbh-border-soft); border-radius: 16px;
            cursor: pointer; font: inherit; color: inherit;
            transition: border-color .15s ease, background .15s ease, transform .15s ease, box-shadow .15s ease;
            box-shadow: var(--tmdbh-shadow-sm);
        }
        .tmdbh-candidate:hover { border-color: color-mix(in srgb, var(--tmdbh-accent) 55%, transparent); transform: translateY(-2px); box-shadow: 0 12px 28px rgba(35, 45, 85, .22), 0 0 0 1px color-mix(in srgb, var(--tmdbh-accent) 22%, transparent); }
        .tmdbh-root[data-theme="dark"] .tmdbh-candidate:hover { box-shadow: 0 12px 28px rgba(0, 0, 0, .5), 0 0 0 1px color-mix(in srgb, var(--tmdbh-accent) 30%, transparent); }
        .tmdbh-candidate.selected { border-color: var(--tmdbh-accent); box-shadow: 0 0 0 2px var(--tmdbh-accent-soft), var(--tmdbh-shadow-sm); }
        .tmdbh-candidate .tmdbh-cand-poster { width: 76px; aspect-ratio: 2/3; border-radius: 10px; overflow: hidden; background: var(--tmdbh-glass-soft); box-shadow: 0 6px 16px rgba(35, 45, 85, .2); }
        .tmdbh-root[data-theme="dark"] .tmdbh-candidate .tmdbh-cand-poster { box-shadow: 0 6px 16px rgba(0, 0, 0, .45); }
        .tmdbh-candidate .tmdbh-cand-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .tmdbh-candidate strong { display: block; font-size: 12.5px; font-weight: 700; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .tmdbh-candidate small { display: block; margin-top: 3px; color: var(--tmdbh-muted); font-size: 10.5px; }
        .tmdbh-candidate p { margin: 5px 0 0; color: var(--tmdbh-muted); font-size: 10.5px; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .tmdbh-compare-table { width: 100%; border-collapse: collapse; margin-top: 4px; font-size: 12px; }
        .tmdbh-compare-table th, .tmdbh-compare-table td { border-bottom: 1px solid var(--tmdbh-border-soft); padding: 6px 8px; text-align: left; vertical-align: top; word-break: break-all; }
        .tmdbh-compare-table th { color: var(--tmdbh-muted); font-weight: 600; font-size: 11px; background: var(--tmdbh-glass-soft); white-space: nowrap; }
        .tmdbh-compare-table .cmp-key { white-space: nowrap; font-weight: 600; min-width: 88px; }
        .tmdbh-compare-table .cmp-official { color: var(--tmdbh-muted); max-width: 260px; }
        .tmdbh-compare-table .cmp-source { max-width: 300px; }
        .tmdbh-compare-table tr.same .cmp-source { color: var(--tmdbh-muted); }
        .tmdbh-compare-table input[type="checkbox"] { accent-color: var(--tmdbh-accent); width: auto; margin: 0; }
        /* —— 表单控件：透光输入 + 聚焦光晕 —— */
        .tmdbh-body input, .tmdbh-body select, .tmdbh-detail input, .tmdbh-detail select, .tmdbh-detail textarea, .tmdbh-pane input, .tmdbh-pane select, .tmdbh-pane textarea {
            width: 100%; border: 1px solid var(--tmdbh-border-soft); border-radius: 10px; padding: 7px 11px;
            font: inherit; color: var(--tmdbh-text); background: var(--tmdbh-field-bg); margin-top: 4px;
            transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
        }
        .tmdbh-detail textarea { min-height: 74px; resize: vertical; }
        .tmdbh-detail input:focus, .tmdbh-detail select:focus, .tmdbh-detail textarea:focus,
        .tmdbh-pane input:focus, .tmdbh-pane select:focus, .tmdbh-pane textarea:focus {
            outline: none; border-color: var(--tmdbh-accent); background: var(--tmdbh-highlight);
            box-shadow: 0 0 0 3.5px var(--tmdbh-accent-soft);
        }
        label.tmdbh-field { display: block; margin-top: 9px; font-size: 12px; color: var(--tmdbh-muted); min-width: 0; }
        .tmdbh-row { display: flex; gap: 8px; align-items: end; margin-top: 10px; flex-wrap: wrap; }
        .tmdbh-row.tmdbh-inline { align-items: center; }
        .tmdbh-btn {
            border: 1px solid var(--tmdbh-border-soft); background: var(--tmdbh-glass-strong); color: var(--tmdbh-text);
            border-radius: 10px; padding: 7px 15px; cursor: pointer; font: inherit; font-weight: 600; position: relative;
            transition: all .18s ease; text-decoration: none; display: inline-block;
            box-shadow: var(--tmdbh-shadow-sm);
        }
        .tmdbh-btn:hover { background: var(--tmdbh-highlight); border-color: color-mix(in srgb, var(--tmdbh-accent) 55%, transparent); color: var(--tmdbh-accent); }
        .tmdbh-btn:active { transform: scale(.985); }
        .tmdbh-btn.primary {
            background: linear-gradient(135deg, var(--tmdbh-accent), var(--tmdbh-accent-2));
            color: var(--tmdbh-accent-ink); border-color: transparent;
            box-shadow: 0 6px 16px var(--tmdbh-accent-glow), inset 0 1px 0 rgba(255, 255, 255, .35);
        }
        .tmdbh-btn.primary:hover { background: linear-gradient(135deg, color-mix(in srgb, var(--tmdbh-accent) 88%, white), color-mix(in srgb, var(--tmdbh-accent-2) 88%, white)); color: var(--tmdbh-accent-ink); border-color: transparent; }
        .tmdbh-btn:disabled { opacity: .4; cursor: not-allowed; filter: none; transform: none; }
        .tmdbh-btn.compact { padding: 3px 10px; font-size: 11px; border-radius: 8px; box-shadow: none; }
        .tmdbh-btn.danger { color: var(--tmdbh-err); }
        .tmdbh-btn.loading { color: transparent !important; pointer-events: none; }
        .tmdbh-btn.loading::after {
            content: ""; position: absolute; inset: 0; margin: auto; width: 14px; height: 14px;
            border: 2px solid rgba(127, 127, 127, .3); border-top-color: var(--tmdbh-accent);
            border-radius: 50%; animation: tmdbh-spin .8s linear infinite;
        }
        .tmdbh-btn.primary.loading::after { border-top-color: var(--tmdbh-accent-ink); }
        @keyframes tmdbh-spin { to { transform: rotate(360deg); } }
        .tmdbh-hint { color: var(--tmdbh-muted); font-size: 12px; margin-top: 7px; }
        .tmdbh-hint.muted { color: var(--tmdbh-muted); }
        /* 红果搜索候选与剧集信息卡 */
        .tmdbh-site-cands { display: grid; gap: 5px; margin-top: 8px; }
        .tmdbh-site-cand {
            display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 10px; align-items: center;
            padding: 7px 9px; text-align: left; cursor: pointer; font: inherit; color: inherit;
            background: var(--tmdbh-card-bg); border: 1px solid var(--tmdbh-border-soft); border-radius: 12px;
            transition: border-color .15s ease, background .15s ease;
        }
        .tmdbh-site-cand:hover { border-color: var(--tmdbh-accent); background: var(--tmdbh-accent-soft); }
        .tmdbh-site-cand img, .tmdbh-site-cand .tmdbh-site-cand-noimg { width: 44px; height: 58px; object-fit: cover; border-radius: 7px; background: var(--tmdbh-glass-soft); display: block; }
        .tmdbh-site-cand-copy { min-width: 0; display: grid; gap: 2px; }
        .tmdbh-site-cand-copy strong { font-size: 12px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .tmdbh-site-cand-copy small { color: var(--tmdbh-muted); font-size: 10.5px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .tmdbh-site-meta { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; margin-top: 8px; padding: 10px; background: var(--tmdbh-card-bg); border: 1px solid var(--tmdbh-border-soft); border-radius: 14px; box-shadow: var(--tmdbh-shadow-sm); }
        .tmdbh-site-meta img { width: 64px; border-radius: 8px; cursor: pointer; display: block; box-shadow: 0 6px 14px rgba(35, 45, 85, .2); }
        .tmdbh-root[data-theme="dark"] .tmdbh-site-meta img { box-shadow: 0 6px 14px rgba(0, 0, 0, .45); }
        .tmdbh-site-meta-copy { min-width: 0; display: grid; gap: 3px; align-content: start; justify-items: start; }
        .tmdbh-site-meta-copy b { font-size: 12.5px; }
        .tmdbh-site-meta-copy small { color: var(--tmdbh-muted); font-size: 11px; line-height: 1.5; }
        .tmdbh-divider { border: none; border-top: 1px solid var(--tmdbh-border-soft); margin: 12px 0 4px; }
        .tmdbh-status {
            margin-top: 9px; font-size: 12px; color: var(--tmdbh-text); white-space: pre-wrap; word-break: break-all;
            background: var(--tmdbh-glass-soft); border: 1px solid var(--tmdbh-border-soft); border-radius: 10px; padding: 8px 11px; display: none;
        }
        .tmdbh-status.show { display: block; }
        .tmdbh-status.ok { color: var(--tmdbh-ok); background: rgba(20, 154, 79, .1); border-color: color-mix(in srgb, var(--tmdbh-ok) 30%, transparent); }
        .tmdbh-root[data-theme="dark"] .tmdbh-status.ok { background: rgba(74, 222, 128, .1); }
        .tmdbh-status.err { color: var(--tmdbh-err); background: rgba(217, 45, 32, .08); border-color: color-mix(in srgb, var(--tmdbh-err) 28%, transparent); }
        .tmdbh-root[data-theme="dark"] .tmdbh-status.err { background: rgba(248, 113, 113, .1); }
        .tmdbh-list { margin-top: 8px; border: 1px solid var(--tmdbh-border-soft); border-radius: 12px; overflow: hidden; max-height: 200px; overflow-y: auto; background: var(--tmdbh-field-bg); }
        .tmdbh-list .item { padding: 8px 11px; cursor: pointer; border-bottom: 1px solid var(--tmdbh-border-soft); display: flex; gap: 8px; align-items: baseline; transition: background .12s; }
        .tmdbh-list .item:last-child { border-bottom: none; }
        .tmdbh-list .item:hover { background: var(--tmdbh-accent-soft); }
        .tmdbh-list .meta { color: var(--tmdbh-muted); font-size: 12px; }
        /* 分集表格 */
        .tmdbh-table { width: 100%; min-width: 520px; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
        .tmdbh-table-wrap { overflow: auto; border: .5px solid var(--tmdbh-border-soft); border-radius: 12px; max-height: 46vh; background: var(--tmdbh-glass-soft); }
        .tmdbh-table th, .tmdbh-table td { border-bottom: 1px solid var(--tmdbh-border-soft); padding: 4px 6px; text-align: left; vertical-align: middle; word-break: break-all; }
        .tmdbh-table th { color: var(--tmdbh-muted); font-weight: 600; background: var(--tmdbh-card-bg); white-space: nowrap; position: sticky; top: 0; backdrop-filter: blur(10px); }
        .tmdbh-table tbody tr:hover { background: var(--tmdbh-accent-soft); }
        .tmdbh-table td.num { text-align: right; white-space: nowrap; }
        .tmdbh-table input.tmdbh-cell { margin-top: 0; padding: 3px 6px; font-size: 12px; border-radius: 6px; }
        .tmdbh-table .cell-date { min-width: 92px; }
        .tmdbh-table .cell-runtime { min-width: 52px; }
        .tmdbh-table .cell-overview { min-width: 110px; }
        .tmdbh-table .cell-still { min-width: 150px; }
        .tmdbh-table tr.exists td { color: var(--tmdbh-warn-ink); }
        .tmdbh-table tr.dup td { color: var(--tmdbh-err); }
        .tmdbh-epstatus { display: inline-block; border-radius: 999px; padding: 0 8px; font-size: 11px; background: var(--tmdbh-glass-soft); color: var(--tmdbh-muted); white-space: nowrap; }
        .tmdbh-epstatus.ok { background: rgba(20, 154, 79, .13); color: var(--tmdbh-ok); }
        .tmdbh-root[data-theme="dark"] .tmdbh-epstatus.ok { background: rgba(74, 222, 128, .15); }
        .tmdbh-epstatus.err { background: rgba(217, 45, 32, .11); color: var(--tmdbh-err); }
        .tmdbh-root[data-theme="dark"] .tmdbh-epstatus.err { background: rgba(248, 113, 113, .14); }
        .tmdbh-progress { height: 6px; border-radius: 999px; background: var(--tmdbh-glass-soft); overflow: hidden; margin-top: 8px; }
        .tmdbh-progress > div { height: 100%; width: 0; background: linear-gradient(90deg, var(--tmdbh-accent-3), var(--tmdbh-accent), var(--tmdbh-accent-2)); border-radius: 999px; transition: width .25s; }
        .tmdbh-check { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--tmdbh-muted); cursor: pointer; user-select: none; }
        .tmdbh-check input { width: auto; margin: 0 !important; accent-color: var(--tmdbh-accent); }
        .tmdbh-poster { display: flex; gap: 10px; align-items: flex-start; margin-top: 8px; }
        .tmdbh-poster img { width: 76px; border-radius: 10px; border: .5px solid var(--tmdbh-border-soft); box-shadow: var(--tmdbh-shadow-sm); }
        .tmdbh-ref {
            margin: 0 0 10px; border: .5px solid var(--tmdbh-border-soft); border-left: 3px solid var(--tmdbh-accent);
            border-radius: 12px; padding: 8px 11px; font-size: 12px; background: var(--tmdbh-glass-soft);
        }
        .tmdbh-ref b { font-size: 12px; }
        .tmdbh-ref .line { margin-top: 2px; color: var(--tmdbh-text); word-break: break-all; }
        .tmdbh-ref .muted { color: var(--tmdbh-muted); }
        /* 通知：玻璃 HUD 胶囊 */
        .tmdbh-toast {
            position: fixed; z-index: 2147483647; left: 50%; bottom: 40px; transform: translateX(-50%);
            background: var(--tmdbh-glass-strong); color: var(--tmdbh-text);
            border: 1px solid var(--tmdbh-border-soft);
            backdrop-filter: blur(24px) saturate(170%); -webkit-backdrop-filter: blur(24px) saturate(170%);
            border-radius: 14px; padding: 10px 18px;
            font: 13px/1.4 var(--tmdbh-font); display: none; max-width: 70vw;
            box-shadow: var(--tmdbh-shadow); animation: tmdbh-toast-in .18s ease; white-space: pre-wrap;
        }
        @keyframes tmdbh-toast-in { from { opacity: 0; transform: translate(-50%, 8px); } }
        .tmdbh-toast.err { background: rgba(217, 45, 32, .88); border-color: transparent; color: #fff; }
        .tmdbh-root[data-theme="dark"] .tmdbh-toast.err { background: rgba(248, 113, 113, .85); }
        .tmdbh-toast.ok { background: rgba(20, 154, 79, .86); border-color: transparent; color: #fff; }
        .tmdbh-root[data-theme="dark"] .tmdbh-toast.ok { background: rgba(74, 222, 128, .82); }
        details.tmdbh-gen { margin-top: 10px; border: .5px solid var(--tmdbh-border-soft); border-radius: 12px; padding: 8px 10px; background: var(--tmdbh-field-bg); }
        details.tmdbh-gen summary { cursor: pointer; font-size: 12px; color: var(--tmdbh-accent); user-select: none; font-weight: 600; }
        details.tmdbh-more { margin-top: 2px; border: .5px solid var(--tmdbh-border-soft); border-radius: 12px; padding: 6px 10px; }
        details.tmdbh-more summary { cursor: pointer; font-size: 12px; color: var(--tmdbh-muted); user-select: none; }
        .tmdbh-linkrow a { color: var(--tmdbh-accent); }
        .tmdbh-kbd { font: 11px/1 ui-monospace, "SF Mono", SFMono-Regular, monospace; background: var(--tmdbh-glass-soft); border: .5px solid var(--tmdbh-border-soft); border-radius: 5px; padding: 1px 5px; color: var(--tmdbh-muted); }
        .tmdbh-winfoot {
            display: flex; align-items: center; justify-content: space-between; gap: 12px; flex: none;
            padding: 10px 16px; border-top: 1px solid var(--tmdbh-border-soft); background: var(--tmdbh-glass-soft);
        }
        .tmdbh-winfoot .tmdbh-foot-note { color: var(--tmdbh-muted); font-size: 11px; }
        .tmdbh-winfoot .tmdbh-foot-actions { display: flex; gap: 8px; }
        .tmdbh-empty { display: grid; place-items: center; gap: 6px; padding: 40px 0; color: var(--tmdbh-muted); font-size: 13px; }
        .tmdbh-body::-webkit-scrollbar, .tmdbh-detail::-webkit-scrollbar, .tmdbh-pane::-webkit-scrollbar, .tmdbh-fcard-list::-webkit-scrollbar, .tmdbh-table-wrap::-webkit-scrollbar { width: 8px; height: 8px; }
        .tmdbh-body::-webkit-scrollbar-thumb, .tmdbh-detail::-webkit-scrollbar-thumb, .tmdbh-pane::-webkit-scrollbar-thumb, .tmdbh-fcard-list::-webkit-scrollbar-thumb, .tmdbh-table-wrap::-webkit-scrollbar-thumb { background: color-mix(in srgb, var(--tmdbh-muted) 38%, transparent); border-radius: 999px; }
        .tmdbh-body::-webkit-scrollbar-thumb:hover, .tmdbh-detail::-webkit-scrollbar-thumb:hover, .tmdbh-pane::-webkit-scrollbar-thumb:hover, .tmdbh-fcard-list::-webkit-scrollbar-thumb:hover, .tmdbh-table-wrap::-webkit-scrollbar-thumb:hover { background: color-mix(in srgb, var(--tmdbh-muted) 60%, transparent); }
        .tmdbh-body::-webkit-scrollbar-track, .tmdbh-detail::-webkit-scrollbar-track, .tmdbh-pane::-webkit-scrollbar-track, .tmdbh-fcard-list::-webkit-scrollbar-track { background: transparent; }
        @media (max-width: 900px) {
            .tmdbh-window { min-width: 340px; }
            .tmdbh-layout { grid-template-columns: 1fr; grid-template-rows: minmax(150px, 36%) minmax(0, 1fr); }
            .tmdbh-overview { border-right: none; border-bottom: 1px solid var(--tmdbh-border-soft); }
        }
    `;

    function tmdbhContextLabel(context) {
        const kind = context.kind;
        if (kind === "movie-new") return "新增电影";
        if (kind === "tv-new") return "新增剧集";
        if (kind === "movie-edit") return "编辑电影";
        if (kind === "tv-edit") return "编辑剧集";
        if (kind === "episode-edit") return `单集编辑 S${context.seasonNumber}E${context.episodeNumber}`;
        if (kind === "season-edit") return `季编辑器 S${context.seasonNumber}`;
        if (kind === "episode-images") return `单集剧照 S${context.seasonNumber}E${context.episodeNumber}`;
        if (kind === "season-images") return `季图片 S${context.seasonNumber}`;
        if (kind === "season-detail") return `季详情 S${context.seasonNumber}`;
        if (kind === "images") return "图片上传";
        if (kind === "movie-detail") return "电影详情";
        if (kind === "tv-detail") return "剧集详情";
        if (kind === "episode-group-view") return "剧集组";
        if (kind === "episode-group-edit") return "编辑剧集组";
        return "本页未适配";
    }

    const TMDBH_VIEW_NAMES = { search: "搜索", episodes: "批量单集", groups: "剧集组", upload: "上传图片" };

    const TMDBH_SOURCE_NAMES = { douban: "豆瓣", imdb: "IMDb", tmdb: "TMDB", text: "粘贴文本", baike: "百度百科" };

    function createTmdbhPanel(env) {
        const { configStore, storage, pageContext, sources, episodeActions } = env;
        const version = (typeof GM_info !== "undefined" && GM_info && GM_info.script && GM_info.script.version) || "?";

        const state = {
            view: tmdbhViewsForContext(pageContext)[0],
            sourceTab: "douban",
            searchQuery: { douban: "", imdb: "", text: "" },
            candidates: [],
            record: null,
            recordError: "",
            entryTextDraft: "",
            mediaChoice: (pageContext.kind === "tv-new" || pageContext.kind === "tv-detail" || pageContext.kind === "season-edit" || pageContext.kind === "season-detail" || pageContext.kind === "tv-edit" || pageContext.kind === "episode-edit" ? "tv" : "movie"),
            poster: { url: "", file: null, objectUrl: "" },
            posterCropHalf: "",
            entryPoster: "",
            groups: [],
            groupsLoading: false,
            groupsError: "",
            groupDetails: {},
            subGroupEpisodes: {},
            subGroupPicker: null,
            groupDraftName: "",
            groupDraftDesc: "",
            groupDraftType: 1,
            episodeText: "",
            siteUrlQuery: "",
            siteCandidates: [],
            siteMeta: null,
            episodes: [],
            existingNumbers: new Set(),
            duplicateNumbers: new Set(),
            epSelected: new Map(),
            epStatus: new Map(),
            stillStatus: new Map(),
            submitting: false,
            stopRequested: false,
            finished: false,
            progress: 0,
            progressText: "",
            language: "",
            seasonOverride: null,
            lastFailed: [],
            nextEpisode: 1,
            existingIndex: {},
            posterUploaded: false,
            manualImage: null,
            baikeEpisodes: [],
            baikeEpisodesError: "",
            schedule: { pattern: "weekly", startNumber: 1, count: 12, perSlot: 1, weekdays: "一", intervalDays: 7, startDate: "", titleTemplate: "第{n}集", runtime: 0 },
            loading: {}
        };

        const host = document.createElement("div");
        host.dataset.tmdbHelper = "root";
        (document.body || document.documentElement).appendChild(host);
        const shadow = host.attachShadow({ mode: "open" });

        const style = document.createElement("style");
        style.textContent = TMDBH_UI_STYLES;
        shadow.appendChild(style);

        const root = document.createElement("div");
        root.className = "tmdbh-root";
        root.innerHTML = `
            <div class="tmdbh-ball" data-action="toggle" title="TMDB 助手（左键：打开右侧面板；右键：复制 名称 (年份) {tmdbid=…}）">TMDB<br>助手</div>
            <div class="tmdbh-overlay" data-role="overlay">
                <div class="tmdbh-window" data-role="window">
                    <div class="tmdbh-resizer" data-role="resizer" title="拖动调整面板宽度"></div>
                    <div class="tmdbh-winhead" data-role="head"></div>
                    <div class="tmdbh-viewtabs" data-role="viewtabs"></div>
                    <div class="tmdbh-winbody" data-role="body"></div>
                    <div class="tmdbh-winfoot" data-role="foot"></div>
                </div>
            </div>
            <div class="tmdbh-toast" data-role="toast"></div>
        `;
        shadow.appendChild(root);

        const ball = shadow.querySelector(".tmdbh-ball");
        const overlayEl = shadow.querySelector('[data-role="overlay"]');
        const headEl = shadow.querySelector('[data-role="head"]');
        const viewTabsEl = shadow.querySelector('[data-role="viewtabs"]');
        const bodyEl = shadow.querySelector('[data-role="body"]');
        const footEl = shadow.querySelector('[data-role="foot"]');
        const toastEl = shadow.querySelector('[data-role="toast"]');

        toastEl.addEventListener("click", () => { toastEl.style.display = "none"; });

        let toastTimer = 0;
        function toast(message, type) {
            const tone = type === true ? "err" : type || "";
            toastEl.textContent = `${tone === "err" ? "✕ " : tone === "ok" ? "✓ " : ""}${message}`;
            toastEl.className = `tmdbh-toast ${tone}`;
            toastEl.style.display = "block";
            clearTimeout(toastTimer);
            toastTimer = setTimeout(() => { toastEl.style.display = "none"; }, 4200);
        }

        function applyTheme() {
            let theme = configStore.get().theme;
            if (theme === "auto") {
                theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
            }
            root.dataset.theme = theme === "dark" ? "dark" : "light";
        }

        function isLoading(action) {
            return Boolean(state.loading[action]);
        }

        function setLoading(action, value) {
            if (value) state.loading[action] = true;
            else delete state.loading[action];
            renderBody();
            renderHead();
        }

        function restoreBallPosition() {
            const config = configStore.get();
            const x = Number.isFinite(config.panel.x) ? config.panel.x : window.innerWidth - 76;
            const y = Number.isFinite(config.panel.y) ? config.panel.y : Math.round(window.innerHeight * 0.3);
            ball.style.left = `${Math.max(4, Math.min(window.innerWidth - 60, x))}px`;
            ball.style.top = `${Math.max(4, Math.min(window.innerHeight - 60, y))}px`;
        }

        function setOpen(next) {
            const open = Boolean(next);
            if (open === overlayEl.classList.contains("open")) return;
            overlayEl.classList.toggle("open", open);
            if (open) render();
        }

        function renderHead() {
            const viewName = TMDBH_VIEW_NAMES[state.view] || "搜索";
            headEl.innerHTML = `
                <span class="tmdbh-logo">T</span>
                <div class="tmdbh-wintitle">
                    <h2>TMDB 助手<span class="tmdbh-ver">v${tmdbhEsc(version)}</span></h2>
                    <p>${tmdbhEsc(tmdbhContextLabel(pageContext))} · ${tmdbhEsc(viewName)}</p>
                </div>
                <div class="tmdbh-head-tools">
                    <button class="tmdbh-iconbtn" data-action="collapse" title="收起到边缘">»</button>
                    <button class="tmdbh-iconbtn" data-action="close" title="收起（Esc）">✕</button>
                </div>
            `;
        }

        function renderViewTabs() {
            const views = tmdbhViewsForContext(pageContext) || [];
            if (!views.includes(state.view)) state.view = views[0];
            if (views.length <= 1) {
                viewTabsEl.innerHTML = "";
                viewTabsEl.style.display = "none";
                return;
            }
            viewTabsEl.style.display = "";
            viewTabsEl.innerHTML = views
                .map((view) => `<button data-action="view" data-view="${view}" class="${state.view === view ? "active" : ""}">${TMDBH_VIEW_NAMES[view] || view}</button>`)
                .join("");
        }

        function renderFoot() {
            footEl.innerHTML = `
                <span class="tmdbh-foot-note">本浮层只查资料、不改动 TMDB 页面。<span class="tmdbh-kbd">Esc</span> 关闭，<span class="tmdbh-kbd">Alt+T</span> 隐藏悬浮球，右键悬浮球直接复制「名称 (年份) {tmdbid=…}」。</span>
            `;
        }

        // —— 右栏：数据源选择与查询 ——
        function renderApiKeyInline() {
            const cfg = configStore.get();
            const savedKey = String(cfg.tmdb && cfg.tmdb.apiKey || "").trim();
            if (savedKey && !state.apiKeyEditing) {
                const masked = savedKey.length > 10 ? `${savedKey.slice(0, 5)}***${savedKey.slice(-4)}` : "***";
                return `
                    <div class="tmdbh-row" style="margin-top:6px;align-items:center;">
                        <span class="tmdbh-chip ok" style="flex:none;">✓ Key 已配置（${tmdbhEsc(masked)}）</span>
                        <button class="tmdbh-btn compact" data-action="edit-api-key">更换</button>
                        <button class="tmdbh-btn compact" data-action="clear-api-key">清除</button>
                    </div>
                    <div class="tmdbh-hint" style="margin-top:2px;">免费 API Key：themoviedb.org → 设置 → API。Key 失效（HTTP 401）时点「更换」。</div>
                `;
            }
            return `
                <div class="tmdbh-row" style="margin-top:6px;">
                    <input data-role="api-key" type="password" placeholder="${savedKey ? "粘贴新的 TMDB API Key (v3)" : "粘贴 TMDB API Key (v3)"}" style="flex:1;margin-top:0;">
                    <button class="tmdbh-btn primary compact" data-action="save-api-key" style="flex:none;">保存</button>
                    ${savedKey ? '<button class="tmdbh-btn compact" data-action="cancel-edit-api-key" style="flex:none;">取消</button>' : ""}
                </div>
                <div class="tmdbh-hint" style="margin-top:2px;">IMDb 反查需要免费 API Key：themoviedb.org → 设置 → API。</div>
            `;
        }

        function sourceHint(tab) {
            if (tab === "douban") return "支持 关键词 / 条目链接 / IMDb 编号 / subject 数字 ID";
            if (tab === "imdb") return "输入 tt 编号：经 TMDB find 反查条目（可直接反查豆瓣）";
            if (tab === "text") return "「标题：xxx / 年份：2024 / 简介：……」或「标题 | 年份 | 简介」均可";
            if (tab === "baike") return "支持 关键词 / 百科条目链接（baike.baidu.com/item/…），载入后可一键抓分集剧情、上传海报";
            const adapter = sources.get(tab);
            return (adapter && adapter.hint) || "自定义数据源（registerDataSource 注册）";
        }

        function sourceDisplayName(tab) {
            return TMDBH_SOURCE_NAMES[tab] || (sources.get(tab) && sources.get(tab).name) || tab;
        }

        function renderSourceBar() {
            const builtinTabs = ["douban", "baike", "imdb", "text"];
            const customTabs = sources.list().map((adapter) => adapter.id).filter((id) => !builtinTabs.includes(id));
            const tabs = [...builtinTabs, ...customTabs];
            const active = state.sourceTab;
            const query = state.searchQuery[active] || "";
            const searchBtnLabel = active === "text" ? "解析文本" : "搜索";
            return `
                <div class="tmdbh-section">
                    <div class="tmdbh-sec-title">
                        <h4>数据来源</h4>
                        <span>${tmdbhEsc(sourceHint(active))}</span>
                    </div>
                    <div class="tmdbh-srcbar">
                        ${tabs.map((tab) => `<button data-action="source-tab" data-source-tab="${tmdbhEscAttr(tab)}" class="${active === tab ? "active" : ""}">${tmdbhEsc(sourceDisplayName(tab))}</button>`).join("")}
                    </div>
                    ${active === "text" ? `
                        <label class="tmdbh-field">条目文本
                            <textarea data-role="entry-text" placeholder="标题：xxx&#10;原名：xxx&#10;年份：2024&#10;首播：2024-01-01&#10;类型：剧情/科幻&#10;导演：xx&#10;主演：xx / yy&#10;简介：……">${tmdbhEsc(state.entryTextDraft)}</textarea>
                        </label>
                        <div class="tmdbh-row">
                            <button class="tmdbh-btn ${isLoading("parse-clipboard") ? "loading" : ""}" data-action="parse-clipboard" ${isLoading("parse-clipboard") ? "disabled" : ""}>📋 从剪贴板解析</button>
                            <button class="tmdbh-btn primary" data-action="parse-entry" title="快捷键 Ctrl+Enter">解析并载入</button>
                        </div>
                    ` : `
                        <div class="tmdbh-row" style="margin-top:8px;">
                            <input data-role="source-query" value="${tmdbhEscAttr(query)}" placeholder="${active === "imdb" ? "tt0111161" : "搜索关键词 / 条目链接 / ID"}" style="flex:1;margin-top:0;">
                            <button class="tmdbh-btn primary ${isLoading(`search-${active}`) ? "loading" : ""}" data-action="source-search" ${isLoading(`search-${active}`) ? "disabled" : ""}>${searchBtnLabel}</button>
                        </div>
                        ${active === "imdb" ? renderApiKeyInline() : ""}
                    `}
                    <div data-role="candidates"></div>
                </div>
            `;
        }

        function recordChips(record) {
            const rec = record || {};
            const chips = [];
            if (rec.year) chips.push(`<span class="tmdbh-chip accent">${tmdbhEsc(rec.year)}</span>`);
            if (rec.rating) chips.push(`<span class="tmdbh-chip ok">★ ${tmdbhEsc(rec.rating)}</span>`);
            for (const genre of toList(rec.genres).slice(0, 4)) chips.push(`<span class="tmdbh-chip accent">${tmdbhEsc(genre)}</span>`);
            for (const country of toList(rec.countries).slice(0, 3)) chips.push(`<span class="tmdbh-chip">${tmdbhEsc(country)}</span>`);
            if (rec.runtime) chips.push(`<span class="tmdbh-chip">${rec.runtime} 分钟</span>`);
            if (rec.episodeCount) chips.push(`<span class="tmdbh-chip">${rec.episodeCount} 集</span>`);
            return chips.join("");
        }

        function recordPeopleLines(record) {
            const rec = record || {};
            const line = (label, list) => (Array.isArray(list) && list.length ? `<div class="tmdbh-people"><b>${label}：</b>${tmdbhEsc(list.slice(0, 8).join("、"))}${list.length > 8 ? " 等" : ""}</div>` : "");
            const cast = Array.isArray(rec.cast) ? rec.cast.map((item) => (item && item.character ? `${item.name} 饰 ${item.character}` : item && item.name)).filter(Boolean) : [];
            return `
                ${line("导演", rec.directors)}
                ${line("编剧", rec.writers)}
                ${line("主演", cast)}
                ${line("出品方", rec.companies)}
                ${line("播出平台", rec.networks)}
                ${line("语言", rec.languages)}
            `;
        }

        function renderRecordHeader() {
            const rec = state.record;
            const errorBlock = state.recordError
                ? `<div class="tmdbh-status show err" style="margin-bottom:8px;">✕ ${tmdbhEsc(state.recordError)}</div>`
                : "";
            if (!rec || !rec.title) {
                if (state.recordError) return errorBlock;
                return `<div class="tmdbh-hint">还没有载入来源条目：上方选择数据源并搜索，或用「${TMDBH_SOURCE_NAMES[state.sourceTab]}」直接查询。</div>`;
            }
            const sourceName = { douban: "豆瓣", imdb: "IMDb", tmdb: "TMDB", text: "粘贴文本", baike: "百度百科", page: "当前页" }[rec.source] || rec.source;
            const link = rec.url ? ` <a class="tmdbh-btn compact" href="${tmdbhEscAttr(rec.url)}" target="_blank">来源页</a>` : "";
            const posterBtn = rec.poster && canUploadPosterHere()
                ? `<button class="tmdbh-btn compact ${isLoading("record-poster-upload") ? "loading" : ""}" data-action="record-poster-upload" ${isLoading("record-poster-upload") ? "disabled" : ""} title="抓取来源海报，按 TMDB 官方规范（2:3、JPG）处理后直传到当前条目的海报库">⬆ 上传海报</button>`
                : "";
            const baikeEpsBtn = rec.source === "baike"
                ? `<button class="tmdbh-btn compact ${isLoading("baike-episodes") ? "loading" : ""}" data-action="baike-episodes" ${isLoading("baike-episodes") ? "disabled" : ""} title="抓取百科词条里的分集剧情（集数/集名/简介），可复制或直接填入季编辑器的分集表格">⬇ 抓取分集剧情</button>`
                : "";
            const copyButtons = `
                <div class="tmdbh-chip-row" data-role="copy-row">
                    <button class="tmdbh-btn compact" data-action="copy-field" data-copy="titleYear" title="复制「名称 (年份)」">⧉ 名称 (年份)</button>
                    <button class="tmdbh-btn compact" data-action="copy-field" data-copy="title" title="复制中文名称">⧉ 名称</button>
                    ${rec.originalTitle && rec.originalTitle !== rec.title ? `<button class="tmdbh-btn compact" data-action="copy-field" data-copy="originalTitle" title="复制原始标题">⧉ 原名</button>` : ""}
                    <button class="tmdbh-btn compact" data-action="copy-field" data-copy="date" title="复制上映/首播日期">⧉ 日期</button>
                    ${rec.overview ? `<button class="tmdbh-btn compact" data-action="copy-field" data-copy="overview" title="复制简介全文">⧉ 简介</button>` : ""}
                    ${posterBtn}
                    ${baikeEpsBtn}
                    <button class="tmdbh-btn compact primary" data-action="copy-record" title="复制标题/年份/类型/演职员/简介等全部信息">⧉ 复制全部信息</button>
                </div>
            `;
            return `
                ${errorBlock}
                <div class="tmdbh-media-header">
                    <div class="tmdbh-media-poster">${rec.poster ? `<img src="${tmdbhEscAttr(rec.poster)}" alt="海报" referrerpolicy="no-referrer" data-tmdbh-img="1">` : ""}</div>
                    <div class="tmdbh-media-copy">
                        <h3>${tmdbhEsc(rec.title)}${rec.originalTitle && rec.originalTitle !== rec.title ? `<span style="color:var(--tmdbh-muted);font-weight:400;font-size:13px;">（${tmdbhEsc(rec.originalTitle)}）</span>` : ""}</h3>
                        <p>${tmdbhEsc([sourceName + (rec.sourceId ? ` · ${rec.sourceId}` : ""), rec.date, rec.rating ? `★ ${rec.rating}` : ""].filter(Boolean).join(" ｜ "))}${link}</p>
                        <div class="tmdbh-chip-row">${recordChips(rec)}</div>
                        ${copyButtons}
                        ${rec.overview ? `<p class="tmdbh-media-overview">${tmdbhEsc(rec.overview)}</p>` : ""}
                        ${recordPeopleLines(rec)}
                    </div>
                </div>
                ${renderBaikeEpisodesBlock(rec)}
            `;
        }

        // 百科条目的分集剧情区：抓取结果（集数/集名/简介）+ 复制 TSV / 填入季编辑器分集表格
        function renderBaikeEpisodesBlock(rec) {
            if (!rec || rec.source !== "baike" || !state.baikeEpisodes.length) return "";
            const rows = state.baikeEpisodes;
            const fillBtn = pageContext.kind === "season-edit"
                ? `<button class="tmdbh-btn compact primary" data-action="baike-eps-fill" title="把分集填入「批量单集」的分集表格（可再编辑后批量提交）">⇢ 填入分集表格</button>`
                : "";
            return `
                <div class="tmdbh-section" data-role="baike-eps">
                    <div class="tmdbh-sec-title">
                        <h4>分集剧情（${rows.length} 集）</h4>
                        <span>来源：百科词条「分集剧情」表格</span>
                    </div>
                    <div class="tmdbh-chip-row">
                        <button class="tmdbh-btn compact" data-action="baike-eps-copy" title="复制为 TSV（集数/集名/简介），可直接粘贴到季编辑器的分集解析框">⧉ 复制 TSV</button>
                        ${fillBtn}
                    </div>
                    <div class="tmdbh-table-wrap" style="max-height:240px;">
                        <table class="tmdbh-table">
                            <thead><tr><th class="num" style="width:52px;">集</th><th style="width:140px;">集名</th><th>剧情简介</th></tr></thead>
                            <tbody>
                                ${rows.map((ep) => `<tr><td class="num">${ep.episodeNumber}</td><td>${tmdbhEsc(ep.name || "")}</td><td>${tmdbhEsc(tmdbhTruncate(ep.overview || "", 140))}</td></tr>`).join("")}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        function renderCandidates() {
            const container = bodyEl.querySelector('[data-role="candidates"]');
            if (!container) return;
            const list = state.candidates || [];
            if (!list.length) {
                container.innerHTML = "";
                return;
            }
            container.innerHTML = `
                <div class="tmdbh-candidate-rail">
                    ${list.map((cand, index) => `
                        <div class="tmdbh-candidate ${state.record && state.record.sourceId === cand.sourceId && state.record.source === cand.source ? "selected" : ""}" data-action="candidate-pick" data-cand="${index}" role="button" tabindex="0">
                            <span class="tmdbh-cand-poster">${cand.poster ? `<img src="${tmdbhEscAttr(cand.poster)}" alt="" referrerpolicy="no-referrer" data-tmdbh-img="1">` : ""}</span>
                            <span>
                                <strong>${tmdbhEsc(cand.title || "（未命名）")}</strong>
                                <small>${tmdbhEsc([cand.originalTitle, cand.year, { douban: "豆瓣", imdb: "IMDb", tmdb: "TMDB", text: "文本", baike: "百科" }[cand.source] || cand.source, cand.rating ? `★ ${cand.rating}` : ""].filter(Boolean).join(" · "))}</small>
                                <p>${tmdbhEsc(tmdbhTruncate(cand.overview || toList(cand.genres).join("/") || "", 60))}</p>
                            </span>
                            <button class="tmdbh-btn compact" data-action="candidate-copy" data-cand="${index}" title="复制「${tmdbhEscAttr(recordTitleYear(cand))}」" style="flex:none;align-self:center;">⧉</button>
                        </div>
                    `).join("")}
                </div>
                <div class="tmdbh-hint">点击候选卡片载入详情；点卡片右侧 ⧉ 直接复制「名称 (年份)」。</div>
            `;
        }

        // —— 搜索视图：数据源查询 + 候选 + 来源条目（含复制按钮），无任何写入 ——
        function renderSearchView() {
            return `
                <section class="tmdbh-detail" style="max-width:860px;margin:0 auto;">
                    ${renderSourceBar()}
                    <div class="tmdbh-section">
                        <div class="tmdbh-sec-title">
                            <h4>来源条目</h4>
                            ${state.record && state.record.title ? `<button class="tmdbh-btn compact" data-action="record-clear" title="清掉当前来源条目，重新搜索">✕ 清除来源</button>` : ""}
                        </div>
                        ${renderRecordHeader()}
                    </div>
                </section>
            `;
        }

        function renderSchedulePlan() {
            const s = state.schedule;
            const today = new Date();
            const todayText = `${today.getFullYear()}-${tmdbhPad2(today.getMonth() + 1)}-${tmdbhPad2(today.getDate())}`;
            if (!s.startDate) s.startDate = todayText;
            return `
                <div class="tmdbh-section">
                    <div class="tmdbh-sec-title">
                        <h4>排期生成器</h4>
                        <span>TMDB 规则：真实日历日，同日多集共用日期、集号连续</span>
                    </div>
                    <label class="tmdbh-field">排期模式
                        <select data-gen="pattern">
                            <option value="weekly" ${s.pattern === "weekly" ? "selected" : ""}>每周固定更新日（支持一周多天）</option>
                            <option value="interval" ${s.pattern === "interval" ? "selected" : ""}>固定间隔天数</option>
                        </select>
                    </label>
                    <div class="tmdbh-row" style="margin-top:6px;">
                        <label class="tmdbh-field" style="width:74px;">起始集
                            <input data-gen="startNumber" type="number" min="1" value="${s.startNumber}" title="已根据编辑器内已有集数自动建议">
                        </label>
                        <label class="tmdbh-field" style="width:64px;">集数
                            <input data-gen="count" type="number" min="1" max="500" value="${s.count}">
                        </label>
                        <label class="tmdbh-field" style="width:76px;">每次集数
                            <input data-gen="perSlot" type="number" min="1" max="20" value="${s.perSlot}" title="单日多集：每次更新连发的集数（如动漫一更两集）">
                        </label>
                        <label class="tmdbh-field" style="width:138px;">首播日期
                            <input data-gen="startDate" type="date" value="${tmdbhEscAttr(s.startDate)}">
                        </label>
                        ${s.pattern === "weekly" ? `
                            <label class="tmdbh-field" style="width:130px;">更新日（每周）
                                <input data-gen="weekdays" value="${tmdbhEscAttr(s.weekdays)}" placeholder="一,四（可写 周一/周四）">
                            </label>
                        ` : `
                            <label class="tmdbh-field" style="width:96px;">间隔(天)
                                <input data-gen="intervalDays" type="number" min="1" max="60" value="${s.intervalDays}">
                            </label>
                        `}
                    </div>
                    <div class="tmdbh-row" style="margin-top:6px;">
                        <label class="tmdbh-field" style="flex:1;">标题模板（{n}=集数，{i}=序号，{k}=日内序号）
                            <input data-gen="titleTemplate" value="${tmdbhEscAttr(s.titleTemplate)}">
                        </label>
                        <label class="tmdbh-field" style="width:88px;">单集片长(分)
                            <input data-gen="runtime" type="number" min="0" max="600" value="${s.runtime || ""}">
                        </label>
                    </div>
                    <div class="tmdbh-row">
                        <button class="tmdbh-btn primary" data-action="ep-generate">⟳ 生成排期分集</button>
                        <span class="tmdbh-hint" style="align-self:center;">示例：每周一、周四各更两集 → 模式=每周固定更新日，更新日=一,四，每次集数=2</span>
                    </div>
                </div>
            `;
        }

        function episodeStatusText(ep) {
            if (state.epStatus.has(ep.episodeNumber)) return state.epStatus.get(ep.episodeNumber);
            if (state.duplicateNumbers.has(ep.episodeNumber)) return "输入重复";
            if (state.existingNumbers.has(ep.episodeNumber)) return "已存在";
            return "待提交";
        }

        function renderEpisodesView() {
            const cfg = configStore.get();
            const rows = state.episodes;
            const existingCount = rows.filter((ep) => state.existingNumbers.has(ep.episodeNumber)).length;
            const dupCount = rows.filter((ep) => state.duplicateNumbers.has(ep.episodeNumber)).length;
            const checkedCount = rows.filter((ep) => state.epSelected.get(ep.episodeNumber) === true).length;
            const selExisting = rows.filter((ep) => state.epSelected.get(ep.episodeNumber) === true && state.existingNumbers.has(ep.episodeNumber)).length;
            const missing = findMissingEpisodeNumbers(rows);
            const missingCount = rows.length >= 2 ? missing.length : 0;
            const table = rows.length ? `
                <div class="tmdbh-table-wrap">
                <table class="tmdbh-table">
                    <thead><tr><th style="width:26px;">选</th><th class="num">集</th><th>标题</th><th>播出日期</th><th style="width:52px;">时长</th><th>简介</th><th class="cell-still">缩略图 URL</th><th>状态</th></tr></thead>
                    <tbody>
                        ${rows.map((ep) => {
                            const exists = state.existingNumbers.has(ep.episodeNumber);
                            const dup = state.duplicateNumbers.has(ep.episodeNumber);
                            const status = episodeStatusText(ep);
                            const stillStatus = state.stillStatus.get(ep.episodeNumber) || "";
                            const statusClass = status === "✓" ? "ok" : status.startsWith("✗") ? "err" : "";
                            const overviewLine = String(ep.overview || "").replace(/\s*\n\s*/g, " ").trim();
                            return `<tr class="${exists ? "exists" : ""}${dup ? " dup" : ""}">
                                <td><input type="checkbox" data-ep-check="${ep.episodeNumber}" ${state.epSelected.get(ep.episodeNumber) === true ? "checked" : ""}></td>
                                <td class="num">${ep.episodeNumber}</td>
                                <td><input class="tmdbh-cell" data-ep-name="${ep.episodeNumber}" value="${tmdbhEscAttr(ep.name)}" placeholder="（无标题）"></td>
                                <td><input class="tmdbh-cell cell-date" data-ep-date="${ep.episodeNumber}" value="${tmdbhEscAttr(ep.airDate)}" placeholder="YYYY-MM-DD"></td>
                                <td><input class="tmdbh-cell cell-runtime" data-ep-runtime="${ep.episodeNumber}" type="number" min="0" value="${ep.runtime || ""}"></td>
                                <td><input class="tmdbh-cell cell-overview" data-ep-overview="${ep.episodeNumber}" value="${tmdbhEscAttr(overviewLine)}" title="${tmdbhEscAttr(ep.overview)}" placeholder="（无简介）"></td>
                                <td><input class="tmdbh-cell cell-still" data-ep-still="${ep.episodeNumber}" value="${tmdbhEscAttr(ep.stillUrl || "")}" placeholder="图片直链（可选）" title="分集缩略图（剧照）图片直链；提交缩略图时按 TMDB 官方规范 16:9 ≥1280×720 自动裁切">${stillStatus ? `<div class="tmdbh-epstatus ${stillStatus.startsWith("✗") ? "err" : stillStatus === "✓" ? "ok" : ""}" style="margin-top:2px;">${tmdbhEsc(stillStatus)}</div>` : ""}</td>
                                <td class="ep-status tmdbh-epstatus ${statusClass}" data-ep-status="${ep.episodeNumber}" style="white-space:nowrap;">${tmdbhEsc(status)}</td>
                            </tr>`;
                        }).join("")}
                    </tbody>
                </table>
                </div>
            ` : "";
            const actionableActions = episodeActions.list(episodeActionEnv());
            return `
                <div class="tmdbh-layout">
                <aside class="tmdbh-overview">
                    <div class="tmdbh-ov-summary">
                        <div>
                            <h3>排期与已有集</h3>
                            <p>S${tmdbhEsc(effectiveSeason())} · 语言 ${tmdbhEsc(state.language || tmdbhDetectEditorLanguage())}</p>
                        </div>
                        <div class="tmdbh-ov-status">已有 ${state.existingNumbers.size} 集</div>
                    </div>
                    <div class="tmdbh-pane" style="padding:12px 14px;overflow:auto;">
                        ${renderSchedulePlan()}
                        <div class="tmdbh-row" style="margin-top:8px;">
                            <label class="tmdbh-field" style="width:96px;margin-top:0;">目标季
                                <input data-ep-season type="number" min="0" max="500" value="${tmdbhEsc(effectiveSeason())}">
                            </label>
                            <span class="tmdbh-hint" style="flex:1;align-self:end;">换季后已有集自动刷新。</span>
                        </div>
                        <div class="tmdbh-row" style="margin-top:8px;">
                            <a class="tmdbh-btn" href="${tmdbhEscAttr(seasonImagesUrl(pageContext.tvId, effectiveSeason()))}" target="_blank" title="打开官方季图片页（Media → Posters），助手的「上传图片」直传面板在该页出现">🖼 季海报上传页</a>
                            <span class="tmdbh-hint" style="flex:1;align-self:end;">季海报不在这页传：去官方季图片页，助手在该页提供直传面板。</span>
                        </div>
                        <div class="tmdbh-hint">编辑器内已有 ${tmdbhEsc(state.existingNumbers.size)} 集${existingCount ? `；已存在集提交时自动改走官方更新接口（覆盖）` : ""}${dupCount ? `；输入里有重复集数 ${dupCount} 个（默认只提交首个）` : ""}${missingCount ? `；<b style="color:var(--tmdbh-err);">缺集：${tmdbhEsc(missing.join("、"))}</b>` : ""}${state.nextEpisode > 1 ? `；下一集建议从 ${state.nextEpisode} 开始` : ""}。</div>
                        <hr class="tmdbh-divider">
                        <label class="tmdbh-field">或粘贴分集列表（每行：集数 | 标题 | 日期 | 时长 | 简介 | 缩略图URL，兼容 TSV 与「第01集：标题 2024-01-01」等写法）
                            <textarea data-role="episode-text" placeholder="1&#9;第一集&#9;2024-01-01&#9;45&#9;简介&#9;https://…&#10;2&#9;第二集&#9;2024-01-08">${tmdbhEsc(state.episodeText)}</textarea>
                        </label>
                        <label class="tmdbh-field">过滤词（TMDB-Import 同款：粘贴解析时命中标题的行剔除，剩余集自动重编号、保留原有缺集）
                            <input data-role="ep-filter-words" value="${tmdbhEscAttr(cfg.filters.words)}" placeholder="PV,预告,花絮,特典,NCOP,NCED">
                        </label>
                        <label class="tmdbh-field">或粘贴流媒体平台剧集页链接抓分集（B站 bangumi / 爱奇艺 / 腾讯视频 / 芒果TV / 优酷 / 红果短剧）；直接输入剧名则并行搜索红果 + B站
                            <input data-role="site-url" value="${tmdbhEscAttr(state.siteUrlQuery)}" placeholder="剧名搜索：如 凡人修仙传 ｜ 链接：https://hongguoduanju.com/player/… ｜ https://www.bilibili.com/bangumi/play/ss… ｜ https://www.iqiyi.com/a_…">
                        </label>
                        ${state.siteCandidates.length ? `
                            <div class="tmdbh-site-cands">
                                ${state.siteCandidates.map((cand, index) => `
                                    <button type="button" class="tmdbh-site-cand" data-action="site-cand" data-site-cand="${index}" title="点击抓取该剧整季分集">
                                        ${cand.cover ? `<img src="${tmdbhEscAttr(cand.cover)}" alt="" referrerpolicy="no-referrer">` : `<span class="tmdbh-site-cand-noimg"></span>`}
                                        <span class="tmdbh-site-cand-copy">
                                            <strong>${tmdbhEsc(cand.title || "（未命名）")}</strong>
                                            <small>${tmdbhEsc(cand.platformName || cand.platform || "")}${cand.episodeCnt ? ` · ${cand.episodeCnt} 集` : ""}${cand.intro ? ` · ${tmdbhEsc(tmdbhTruncate(cand.intro, 46))}` : ""}</small>
                                        </span>
                                    </button>
                                `).join("")}
                            </div>` : ""}
                        ${state.siteMeta ? `
                            <div class="tmdbh-site-meta">
                                ${state.siteMeta.cover ? `<img src="${tmdbhEscAttr(state.siteMeta.cover)}" alt="封面" referrerpolicy="no-referrer" data-action="site-cover-copy" title="点击复制封面直链（到上传图片页可直传为海报）" role="button" tabindex="0">` : ""}
                                <div class="tmdbh-site-meta-copy">
                                    <b>${tmdbhEsc(state.siteMeta.title || "（未命名）")}</b>
                                    <small>${tmdbhEsc(state.siteMeta.platform)}${state.siteMeta.episodeCnt ? ` · ${state.siteMeta.episodeCnt} 集` : ""}${state.siteMeta.overview ? ` · ${tmdbhEsc(tmdbhTruncate(state.siteMeta.overview, 60))}` : ""}</small>
                                    ${state.siteMeta.cover ? `<button type="button" class="tmdbh-btn compact" data-action="site-cover-copy">⧉ 复制直链</button>` : ""}
                            ${state.siteMeta.cover && pageContext.tvId != null ? `<button type="button" class="tmdbh-btn compact ${isLoading("cover-upload") ? "loading" : ""}" data-action="site-cover-upload" ${isLoading("cover-upload") ? "disabled" : ""} title="下载封面 → 按 TMDB 规范处理 → 直传为本季海报">⬆ 上传为季海报</button>` : ""}
                                </div>
                            </div>` : ""}
                        <div class="tmdbh-row">
                            <button class="tmdbh-btn" data-action="parse-episodes">解析列表</button>
                            <button class="tmdbh-btn ${isLoading("ep-clipboard") ? "loading" : ""}" data-action="ep-clipboard" ${isLoading("ep-clipboard") ? "disabled" : ""} title="读取剪贴板内容并解析">📋 剪贴板</button>
                            <button class="tmdbh-btn ${isLoading("site-fetch") ? "loading" : ""}" data-action="site-fetch" ${isLoading("site-fetch") ? "disabled" : ""} title="从上面粘贴的平台链接抓取分集列表并填入表格">⤓ 抓平台分集</button>
                            <button class="tmdbh-btn ${isLoading("ep-load-existing") ? "loading" : ""}" data-action="ep-load-existing" ${isLoading("ep-load-existing") ? "disabled" : ""} title="把编辑器里已有的集载入表格，批量修正后覆盖提交">↺ 载入已有集</button>
                            <button class="tmdbh-btn ${isLoading("ep-pull-tmdb") ? "loading" : ""}" data-action="ep-pull-tmdb" ${isLoading("ep-pull-tmdb") ? "disabled" : ""} title="tmdb-scraper 同款：用 TMDB API 读官方季分集（标题/日期/时长/简介/剧照直链），按集号合并进表格，只补空字段、不覆盖手填内容（需 API Key）">⤓ 从 TMDB 拉取本季</button>
                        </div>
                    </div>
                </aside>
                <section class="tmdbh-detail">
                    <div class="tmdbh-section">
                        <div class="tmdbh-sec-title">
                            <h4>分集列表</h4>
                            <span>${rows.length} 集${rows.length ? ` · ${rows[0].airDate || "?"} ~ ${rows[rows.length - 1].airDate || "?"}` : ""}</span>
                        </div>
                        <div class="tmdbh-row" style="margin-top:0;">
                            <button class="tmdbh-btn" data-action="ep-check-all" ${rows.length ? "" : "disabled"}>全选</button>
                            <button class="tmdbh-btn" data-action="ep-check-none" ${rows.length ? "" : "disabled"}>清空</button>
                            ${rows.length ? `<button class="tmdbh-btn" data-action="ep-export-tsv" title="复制为 TSV（集数/标题/日期/时长/简介/缩略图），可回贴到解析框">⇪ 导出 TSV</button>` : ""}
                        </div>
                        ${table || `<div class="tmdbh-empty">左侧生成排期或粘贴分集列表后，这里出现可编辑表格</div>`}
                        ${rows.length ? `
                            <div class="tmdbh-row">
                                ${state.submitting
                                    ? `<button class="tmdbh-btn" data-action="ep-stop">⏹ 停止</button>`
                                    : `<button class="tmdbh-btn primary" data-action="ep-submit" ${checkedCount ? "" : "disabled"}>▶ 提交勾选的 ${checkedCount} 集${selExisting ? `（覆盖 ${selExisting}）` : ""}</button>`}
                                ${state.finished && !state.submitting && state.lastFailed.length ? `<button class="tmdbh-btn" data-action="ep-retry-failed">↻ 重试失败的 ${state.lastFailed.length} 集</button>` : ""}
                                ${state.finished && !state.submitting ? `<button class="tmdbh-btn" data-action="ep-refresh">⟳ 刷新页面核对</button>` : ""}
                            </div>
                            <div class="tmdbh-progress"><div data-role="ep-bar" style="width:${Math.round(state.progress * 100)}%;"></div></div>
                            <div class="tmdbh-hint" data-role="ep-progress-text">${tmdbhEsc(state.progressText || `将提交 ${checkedCount} 集${selExisting ? `（含 ${selExisting} 集覆盖更新）` : ""}，每集间隔 ${cfg.episodes.delayMs}ms、失败重试 ${cfg.episodes.retries} 次；成功过的集号本地留档，刷新后不会重复提交`)}</div>
                        ` : ""}
                        <div class="tmdbh-hint">提交走季编辑器官方内部接口：新集走新增接口，已有集自动改走单集更新接口；逐条串行，表格里可直接改标题/日期/时长/缩略图，改动实时生效。</div>
                    </div>
                    <div class="tmdbh-section">
                        <div class="tmdbh-sec-title">
                            <h4>分集缩略图（剧照）</h4>
                            <span>TMDB 规范：16:9 · ≥1280×720 · JPG，自动居中裁切</span>
                        </div>
                        <div class="tmdbh-row" style="margin-top:0;">
                            <button class="tmdbh-btn ${isLoading("stills-fetch") ? "loading" : ""}" data-action="stills-fetch" ${rows.some((ep) => ep.stillUrl) && !isLoading("stills-fetch") ? "" : "disabled"} title="把表格里填了 URL 的缩略图抓取到本地并按规范预处理">⬇ 抓取并预处理（${rows.filter((ep) => ep.stillUrl).length}）</button>
                            <button class="tmdbh-btn ${isLoading("stills-submit") ? "loading" : ""}" data-action="stills-submit" ${!isLoading("stills-submit") ? "" : "disabled"} title="把已就绪的缩略图直传到 TMDB（POST /image，TvEpisode/still）">⬆ 上传已就绪缩略图</button>
                            <a class="tmdbh-btn" href="${tmdbhEscAttr(episodeStillsPageUrl(pageContext.tvId, effectiveSeason(), 1))}" target="_blank" title="官方单集剧照页（可查看上传结果）">官方剧照页示例</a>
                        </div>
                        <div class="tmdbh-hint">流程：表格里给需要的集填图片直链 → 「抓取并预处理」（低分辨率会明确报错，禁止放大）→ 「上传已就绪缩略图」。新提交的分集需先「提交分集」拿到集 ID 后才能上传。</div>
                    </div>
                    <div class="tmdbh-section">
                        <div class="tmdbh-sec-title">
                            <h4>批量操作</h4>
                            <span>内置动作与 window.TmdbHelper.registerEpisodeAction 注册的扩展共用此区</span>
                        </div>
                        <div class="tmdbh-row" style="margin-top:0;">
                            ${actionableActions.map((action) => `<button class="tmdbh-btn" data-action="episode-action" data-ep-action="${tmdbhEscAttr(action.id)}">${tmdbhEsc(action.title)}</button>`).join("") || `<span class="tmdbh-hint">暂无可用批量操作。</span>`}
                        </div>
                    </div>
                </section>
                </div>
            `;
        }

        function renderApiKeyBlock() {
            const cfg = configStore.get();
            if (tmdbhTmdbReady(cfg)) return "";
            return `
                <div class="tmdbh-ref">
                    <b>TMDB API Key</b>
                    <div class="line muted">用于查询 TMDB 官方数据（新增查重、IMDb 反查、剧集组兜底）。在 themoviedb.org → 设置 → API 免费申请。</div>
                    <label class="tmdbh-field">API Key (v3)
                        <input data-role="api-key" type="password" placeholder="tmdb api key" value="">
                    </label>
                    <div class="tmdbh-row"><button class="tmdbh-btn primary" data-action="save-api-key">保存 Key</button></div>
                </div>
            `;
        }

        function renderGroupsView() {
            const cfg = configStore.get();
            const tvId = pageContext.tvId || pageContext.id;
            const parts = [];
            if (!tmdbhTmdbReady(cfg)) {
                parts.push(renderApiKeyBlock());
            }
            if (pageContext.groupId) {
                parts.push(`
                    <div class="tmdbh-ref">
                        <b>当前剧集组</b>
                        <div class="line">${tmdbhEsc(pageContext.groupId)}</div>
                        <div class="line muted">结构编辑（建子组、拖集排序）可在官方编辑器完成，下方提供入口与结构参考。</div>
                    </div>
                    <div class="tmdbh-row">
                        <a class="tmdbh-btn" href="/tv/${tmdbhEsc(tvId)}/edit/episode_group/${tmdbhEsc(pageContext.groupId)}" target="_blank">✎ 打开官方编辑器</a>
                        <button class="tmdbh-btn" data-action="group-copy-id">⧉ 复制组 ID</button>
                        <button class="tmdbh-btn ${isLoading("group-details") ? "loading" : ""}" data-action="group-details" data-group="${tmdbhEscAttr(pageContext.groupId)}" ${isLoading("group-details") ? "disabled" : ""}>⟳ 读取结构</button>
                    </div>
                `);
            } else if (tvId) {
                parts.push(`
                    <div class="tmdbh-row" style="margin-top:0;">
                        <button class="tmdbh-btn primary ${isLoading("groups-load") ? "loading" : ""}" data-action="groups-load" ${isLoading("groups-load") ? "disabled" : ""}>⟳ 读取该剧的剧集组列表</button>
                        <a class="tmdbh-btn" href="/tv/${tmdbhEsc(tvId)}/edit?active_nav_item=episode_groups" target="_blank">官方剧集组页</a>
                    </div>
                `);
            } else {
                parts.push(`<div class="tmdbh-hint">请在剧集相关的页面上使用剧集组功能。</div>`);
            }
            if (state.groupsError) parts.push(`<div class="tmdbh-status show err">${tmdbhEsc(state.groupsError)}</div>`);
            if (state.groups.length) {
                parts.push(`
                    <div class="tmdbh-table-wrap"><table class="tmdbh-table" style="min-width:0;">
                        <thead><tr><th>名称</th><th>类型</th><th class="num">组/集</th><th>操作</th></tr></thead>
                        <tbody>
                            ${state.groups.map((group) => `
                                <tr>
                                    <td>${tmdbhEsc(group.name || "（未命名）")}<div class="tmdbh-hint" style="margin:0;">${tmdbhEsc(group.description || "")}</div></td>
                                    <td style="white-space:nowrap;">${tmdbhEsc(TMDBH_GROUP_TYPES[group.type] || group.type_name || `类型 ${group.type ?? "?"}`)}</td>
                                    <td class="num">${group.group_count ?? "?"}/${group.episode_count ?? "?"}</td>
                                    <td style="white-space:nowrap;">
                                        <a class="tmdbh-btn compact" href="/tv/${tmdbhEsc(tvId)}/episode_group/${tmdbhEsc(group.id)}" target="_blank">查看</a>
                                        <a class="tmdbh-btn compact" href="/tv/${tmdbhEsc(tvId)}/edit/episode_group/${tmdbhEsc(group.id)}" target="_blank">编辑</a>
                                        <button class="tmdbh-btn compact" data-action="group-details" data-group="${tmdbhEscAttr(group.id)}">结构</button>
                                        <button class="tmdbh-btn compact ${isLoading(`group-edit-${group.id}`) ? "loading" : ""}" data-action="group-edit" data-group="${tmdbhEscAttr(group.id)}">改名</button>
                                        <button class="tmdbh-btn compact danger ${isLoading(`group-del-${group.id}`) ? "loading" : ""}" data-action="group-delete" data-group="${tmdbhEscAttr(group.id)}">删除</button>
                                    </td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table></div>
                `);
            }
            for (const [groupId, details] of Object.entries(state.groupDetails)) {
                const groups = Array.isArray(details.groups) ? details.groups : [];
                const subBlocks = groups.map((sub, index) => {
                    const eps = state.subGroupEpisodes[sub.id];
                    const picker = state.subGroupPicker && state.subGroupPicker.subGroupId === sub.id ? state.subGroupPicker : null;
                    const epsRows = Array.isArray(eps) ? eps.map((ep) => `
                        <div class="tmdbh-row tmdbh-inline" style="margin:2px 0 0 12px;gap:6px;">
                            <span style="flex:1;font-size:12px;">S${tmdbhEsc(ep.season_number)}E${tmdbhEsc(ep.episode_number)}｜${tmdbhEsc(ep.name || "（无标题）")}</span>
                            <button class="tmdbh-btn compact danger ${isLoading(`subgroup-ep-del-${sub.id}-${ep.media_id}`) ? "loading" : ""}" data-action="subgroup-ep-remove" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}" data-media="${tmdbhEscAttr(ep.media_id)}">移除</button>
                        </div>
                    `).join("") : "";
                    const pickerBlock = picker ? `
                        <div class="tmdbh-ref" style="margin-top:6px;">
                            <b>勾选要加入「${tmdbhEsc(sub.name || "子组")}」的单集</b>
                            <div class="tmdbh-row">
                                <label class="tmdbh-field" style="width:90px;margin-top:0;">季号
                                    <input data-picker-season type="number" min="0" max="500" value="${tmdbhEsc(picker.season || 0)}">
                                </label>
                                <button class="tmdbh-btn ${isLoading("picker-load") ? "loading" : ""}" data-action="picker-load" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}" ${isLoading("picker-load") ? "disabled" : ""}>载入该季</button>
                                ${picker.list && picker.list.length ? `<button class="tmdbh-btn primary ${isLoading("picker-add") ? "loading" : ""}" data-action="picker-add" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}" ${isLoading("picker-add") ? "disabled" : ""}>添加勾选的 ${picker.selected.size} 集</button>` : ""}
                                <button class="tmdbh-btn" data-action="picker-cancel">取消</button>
                            </div>
                            ${picker.list ? `
                                <div class="tmdbh-list" style="max-height:200px;">
                                    ${picker.list.map((ep) => {
                                        const mediaId = String(ep.bson_id || ep.media_id || "");
                                        return `<label class="item" style="cursor:pointer;">
                                            <input type="checkbox" data-picker-ep="${tmdbhEscAttr(mediaId)}" ${picker.selected.has(mediaId) ? "checked" : ""} style="width:auto;margin:0 6px 0 0;">
                                            <span>S${tmdbhEsc(ep.season_number)}E${tmdbhEsc(ep.episode_number)}</span>
                                            <span class="meta">${tmdbhEsc(ep.name || "")}</span>
                                        </label>`;
                                    }).join("")}
                                </div>
                                <div class="tmdbh-hint">已在本子组里的集不会出现在列表中；官方接口按 media_id 去重。</div>
                            ` : `<div class="tmdbh-hint">输入季号后点「载入该季」，从官方季数据里勾选单集。</div>`}
                        </div>
                    ` : "";
                    return `<div style="font-size:12px;margin-top:8px;padding-top:6px;border-top:1px dashed var(--tmdbh-border-soft);">
                        ${index + 1}. <b>${tmdbhEsc(sub.name || `组 ${index + 1}`)}</b>（${sub.episode_count ?? (eps ? eps.length : "?")} 集，排序 ${sub.order ?? "-"}）
                        <span style="margin-left:6px;white-space:nowrap;">
                            <button class="tmdbh-btn compact" data-action="subgroup-eps" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}">${isLoading(`subgroup-eps-${sub.id}`) ? "…" : "单集"}</button>
                            <button class="tmdbh-btn compact" data-action="subgroup-picker" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}">＋添加</button>
                            <button class="tmdbh-btn compact ${isLoading(`subgroup-sort-${sub.id}`) ? "loading" : ""}" data-action="subgroup-sort" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}" title="按季/集号升序重排并保存">重排</button>
                            <button class="tmdbh-btn compact" data-action="subgroup-edit" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}">改</button>
                            <button class="tmdbh-btn compact danger" data-action="subgroup-delete" data-group="${tmdbhEscAttr(groupId)}" data-subgroup="${tmdbhEscAttr(sub.id)}">删</button>
                        </span>
                        ${epsRows}
                        ${pickerBlock}
                    </div>`;
                }).join("");
                parts.push(`
                    <details class="tmdbh-gen" open>
                        <summary>结构：${tmdbhEsc(details.name || groupId)}（${groups.length} 个子组）</summary>
                        ${groups.length ? subBlocks : `<div class="tmdbh-hint">该组还没有子组，点下方「添加子组」创建。</div>`}
                        <div class="tmdbh-row">
                            <button class="tmdbh-btn ${isLoading(`subgroup-add-${groupId}`) ? "loading" : ""}" data-action="subgroup-add" data-group="${tmdbhEscAttr(groupId)}">＋ 添加子组</button>
                            ${details.internal === false ? `<span class="tmdbh-hint">（公开 API 数据，登录后可用内部接口增删改）</span>` : ""}
                        </div>
                    </details>
                `);
            }
            parts.push(`
                <hr class="tmdbh-divider">
                <details class="tmdbh-gen">
                    <summary>新增剧集组（直接提交到 TMDB）</summary>
                    <label class="tmdbh-field">组名称
                        <input data-group-name="name" value="${tmdbhEscAttr(state.groupDraftName)}" placeholder="例：流通版播放顺序">
                    </label>
                    <label class="tmdbh-field">描述
                        <textarea data-group-name="desc" style="min-height:52px;" placeholder="说明这个顺序的来源与适用版本">${tmdbhEsc(state.groupDraftDesc)}</textarea>
                    </label>
                    <label class="tmdbh-field">类型
                        <select data-group-name="type">
                            ${Object.entries(TMDBH_GROUP_TYPES).map(([value, label]) => `<option value="${value}" ${Number(value) === state.groupDraftType ? "selected" : ""}>${label}</option>`).join("")}
                        </select>
                    </label>
                    <div class="tmdbh-row">
                        <button class="tmdbh-btn primary ${isLoading("group-create") ? "loading" : ""}" data-action="group-create" ${tvId && !isLoading("group-create") ? "" : "disabled"}>🆕 创建剧集组（直接提交）</button>
                    </div>
                    <div class="tmdbh-hint">已逆向官网内部接口：登录后可创建/改名/删除剧集组与子组，勾选单集加入子组、移除、按季集重排（等价于官方编辑器的拖拽保存）。</div>
                </details>
            `);
            return `<div class="tmdbh-pane">${parts.join("")}</div>`;
        }

        // —— 图片上传页：解析页面内嵌的上传配置，把已抓取的海报直传（免文件选择框） ——
        // 整页 innerHTML 解析开销大，结果按页面缓存一次（页面为传统多页站点，内容不会变化）
        let uploadConfigCache;
        function readUploadConfig() {
            if (uploadConfigCache !== undefined) return uploadConfigCache;
            try {
                uploadConfigCache = parseImageUploadConfig(document.documentElement.innerHTML);
            } catch (err) {
                uploadConfigCache = null;
            }
            return uploadConfigCache;
        }

        function renderUploadView() {
            const config = readUploadConfig();
            const label = config ? ({ TvSeason: "季", TvSeries: "剧集", Movie: "电影", TvEpisode: "单集" }[config.mediaType] || config.mediaType) : "";
            const parts = [];
            parts.push(renderApiKeyBlock());
            parts.push(`
                <div class="tmdbh-ref">
                    <b>图片直传</b>
                    ${config ? `<div class="line">目标：${tmdbhEsc(label)} ｜ media_id：<span style="font-size:11px;">${tmdbhEsc(config.mediaId)}</span> ｜ 类型：${tmdbhEsc(config.type)}</div>` : `<div class="line muted">未在本页解析到上传配置。</div>`}
                    <div class="line muted">直传 = 等价官方「Select files...」+ autoUpload；上传前按 TMDB 官方规范自动处理（比例居中裁剪、等比缩小、格式转换；禁止放大小图）。</div>
                </div>
                <label class="tmdbh-field">图片直链（可选：豆瓣海报 / TMDB 图片直链，自动抓取并升级原图）
                    <input data-role="poster-url" value="${tmdbhEscAttr(state.entryPoster)}" placeholder="https://img1.doubanio.com/view/photo/…">
                </label>
                <div class="tmdbh-row">
                    <label class="tmdbh-field" style="width:150px;margin-top:0;" title="「正面+背面」横向拼图封面：取右半/左半可得到单面竖版海报；TMDB 海报规范为 2:3">封面裁剪
                        <select data-role="poster-crop" style="margin-top:4px;">
                            <option value="">居中裁剪（默认）</option>
                            <option value="right" ${state.posterCropHalf === "right" ? "selected" : ""}>取右半（拼图封面）</option>
                            <option value="left" ${state.posterCropHalf === "left" ? "selected" : ""}>取左半</option>
                        </select>
                    </label>
                    <button class="tmdbh-btn ${isLoading("poster-load") ? "loading" : ""}" data-action="poster-load" ${isLoading("poster-load") ? "disabled" : ""} title="下载直链图片到本地待传；豆瓣/TMDB 小图变体会自动升级原图" style="align-self:flex-end;">⬇ 抓取直链图片</button>
                    <button class="tmdbh-btn primary ${isLoading("upload-direct") ? "loading" : ""}" data-action="upload-direct" ${config && state.poster.file && !isLoading("upload-direct") ? "" : "disabled"}>⬆ 直传（按本页类型自动处理）</button>
                    <button class="tmdbh-btn" data-action="upload-refresh" style="${state.posterUploaded ? "" : "display:none;"}">⟳ 刷新页面</button>
                </div>
                ${state.poster.file ? `<div class="tmdbh-hint">就绪图片：${state.poster.dims ? `${tmdbhEsc(state.poster.dims)}，` : ""}${Math.round(state.poster.file.size / 1024)} KB${state.poster.url ? ` ｜ 来源 ${tmdbhTruncate(state.poster.url, 60)}` : ""}</div>` : ""}
                <hr class="tmdbh-divider">
                <details class="tmdbh-gen">
                    <summary>手动上传本地图片（自选类型，自动校验转换）</summary>
                    <label class="tmdbh-field">图片类型（按 TMDB 官方规范校验）
                        <select data-manual-type>
                            <option value="poster">海报（2:3，500×750 起，最高 2000×3000，JPG）</option>
                            <option value="backdrop">背景图（16:9，1280×720 起，最高 3840×2160，JPG）</option>
                            <option value="still">剧照（16:9，同背景图，JPG）</option>
                            <option value="logo">标志（透明 PNG，200×50 起，最高 2000×2000）</option>
                        </select>
                    </label>
                    <div class="tmdbh-row">
                        <button class="tmdbh-btn" data-action="manual-pick">📁 选择本地图片…</button>
                        <button class="tmdbh-btn primary ${isLoading("manual-upload") ? "loading" : ""}" data-action="manual-upload" ${state.manualImage && config && !isLoading("manual-upload") ? "" : "disabled"}>处理并上传</button>
                    </div>
                    ${state.manualImage ? `<div class="tmdbh-hint">已选：${tmdbhEsc(state.manualImage.name)}（${Math.round(state.manualImage.size / 1024)} KB）——上传时自动裁剪/缩放/转格式；无法满足规范会明确报错。</div>` : `<div class="tmdbh-hint">支持 JPG / PNG / WebP；上传前自动：比例裁剪 → 分辨率放大/缩小 → 格式转换（标志强制 PNG，照片类转 JPG）。</div>`}
                </details>
            `);
            return `<div class="tmdbh-pane">${parts.join("")}</div>`;
        }

        // 同视图重绘时保持滚动位置：点击左侧操作按钮（解析列表/剪贴板/抓平台分集/载入已有集/从 TMDB 拉取本季等）
        // 会触发 renderBody() 整块重绘，若不保留 scrollTop，右栏表格与左栏设置会一起跳回顶部
        let lastRenderedView = null;
        function renderBody() {
            renderViewTabs();
            renderFoot();
            const sameView = state.view === lastRenderedView;
            const scrollerSel = ".tmdbh-detail, .tmdbh-pane, .tmdbh-fcard-list, .tmdbh-table-wrap";
            const positions = sameView ? Array.from(bodyEl.querySelectorAll(scrollerSel)).map((el) => el.scrollTop) : [];
            if (state.view === "episodes") bodyEl.innerHTML = renderEpisodesView();
            else if (state.view === "groups") bodyEl.innerHTML = renderGroupsView();
            else if (state.view === "upload") bodyEl.innerHTML = renderUploadView();
            else bodyEl.innerHTML = renderSearchView();
            if (bodyEl.querySelector('[data-role="candidates"]')) renderCandidates();
            tmdbhHydrateDoubanImages(bodyEl);
            if (sameView) {
                const next = bodyEl.querySelectorAll(scrollerSel);
                next.forEach((el, index) => { if (positions[index] != null) el.scrollTop = positions[index]; });
            }
            lastRenderedView = state.view;
        }

        function render() {
            // 批量单集视图内容多，切宽版停靠台；其余视图窄版
            root.dataset.wide = state.view === "episodes" ? "1" : "0";
            renderHead();
            renderBody();
        }


        // —— 统一数据源运行时：查询 → 候选 → 载入 → 复制/参考 ——
        function applyRecordToState(record) {
            // 换了条目后上一条的百科分集剧情不再适用，一并清掉
            const sameRecord = state.record && record
                && state.record.source === record.source && state.record.sourceId === record.sourceId;
            if (!sameRecord) {
                state.baikeEpisodes = [];
                state.baikeEpisodesError = "";
            }
            state.record = record;
            state.recordError = "";
            renderBody();
            schedulePersistPanelState();
        }

        function doubanCandidateFromSuggestion(item) {
            return createUnifiedRecord({
                source: "douban",
                sourceId: item.id,
                url: item.url || `https://movie.douban.com/subject/${item.id}/`,
                title: item.title,
                originalTitle: item.subTitle && looksLatin(item.subTitle) ? item.subTitle : "",
                year: parseYearValue(item.year),
                aliases: [item.subTitle].filter(Boolean)
            });
        }

        async function runSourceSearch(tab) {
            const input = bodyEl.querySelector('[data-role="source-query"]');
            if (input) state.searchQuery[tab] = input.value.trim();
            const query = state.searchQuery[tab] || "";
            const config = configStore.get();
            const setLoadingKey = `search-${tab}`;
            state.candidates = [];
            state.recordError = "";
            setLoading(setLoadingKey, true);
            try {
                // {tmdbid=123} 标记（123 助手/详情页复制的格式）：直接按 TMDB ID 载入条目
                const tmdbIdMarker = query.match(/tmdbid\s*=\s*(\d+)/i);
                if (tmdbIdMarker) {
                    if (!tmdbhTmdbReady(config)) throw new Error("识别到 tmdbid 标记：需要 TMDB API Key（在「IMDb」来源页签保存）");
                    const id = Number(tmdbIdMarker[1]);
                    const orderedTypes = state.mediaChoice === "movie" ? ["movie", "tv"] : ["tv", "movie"];
                    let record = null;
                    let lastError = null;
                    for (const mediaType of orderedTypes) {
                        try {
                            const detail = await tmdbhTmdbGet(config, `/${mediaType}/${id}`, { append_to_response: "credits" });
                            record = normalizeTmdbDetail(detail, mediaType);
                            break;
                        } catch (err) {
                            lastError = err;
                            if (!/404/.test(err.message)) throw err;
                        }
                    }
                    if (!record) throw new Error(`TMDB 上没有 ID 为 ${id} 的条目${lastError ? `（${lastError.message}）` : ""}`);
                    state.candidates = [record];
                    applyRecordToState(record);
                    toast(`已按 tmdbid 载入：${record.title}`, "ok");
                    return;
                }
                // 自定义数据源（registerDataSource 注册）：search → 候选卡，选中后 findById 载入详情
                const customAdapter = sources.get(tab);
                if (customAdapter && !["douban", "imdb", "text"].includes(tab)) {
                    if (typeof customAdapter.search !== "function") throw new Error(`数据源「${customAdapter.name || tab}」未实现 search(query)`);
                    if (!query) throw new Error("请输入搜索关键词");
                    const results = await customAdapter.search(query, { config });
                    state.candidates = (Array.isArray(results) ? results : []).map((item) => createUnifiedRecord(item));
                    renderBody();
                    toast(state.candidates.length ? `「${customAdapter.name || tab}」找到 ${state.candidates.length} 个候选，点击卡片载入详情` : `「${customAdapter.name || tab}」没有返回候选`, state.candidates.length ? "ok" : "err");
                    return;
                }
                if (tab === "douban") {
                    const imdbMatch = query.match(/^(tt\d+)$/i);
                    const urlMatch = query.match(/movie\.douban\.com\/subject\/(\d+)/) || query.match(/^subject\/(\d+)$/) || query.match(/^(\d{5,})$/);
                    if (urlMatch) {
                        const detail = await fetchDoubanDetail(urlMatch[1], config);
                        const record = normalizeDoubanDetail(detail);
                        state.candidates = [record];
                        applyRecordToState(record);
                        toast(`已载入豆瓣条目：${record.title}${record.rating ? `（${record.rating}）` : ""}`, "ok");
                    } else if (imdbMatch) {
                        const detail = await fetchDoubanDetailByImdb(imdbMatch[1], config);
                        const record = normalizeDoubanDetail(detail);
                        record.imdb = imdbMatch[1].toUpperCase();
                        state.candidates = [record];
                        applyRecordToState(record);
                        toast(`已通过 IMDb 载入豆瓣条目：${record.title}`, "ok");
                    } else if (!query) {
                        throw new Error("请输入搜索关键词");
                    } else {
                        const subjects = await searchDoubanSubjects(query, config);
                        state.candidates = subjects.map(doubanCandidateFromSuggestion);
                        renderBody();
                        toast(subjects.length ? `豆瓣找到 ${subjects.length} 个候选，点击卡片载入详情` : "豆瓣没有返回候选", subjects.length ? "ok" : "err");
                    }
                } else if (tab === "imdb") {
                    if (!/^tt\d+$/i.test(query)) throw new Error("请输入有效的 IMDb 编号（tt 开头）");
                    if (!tmdbhTmdbReady(config)) throw new Error("请先填写 TMDB API Key（IMDb 反查经 TMDB find）");
                    // TMDB /find 对编号区分大小写：TT0111161 会返回 200 + 空结果，必须用小写查询
                    const imdbId = query.toLowerCase();
                    const found = await tmdbhTmdbFindImdb(config, imdbId);
                    const picks = [];
                    for (const item of [...found.tv.slice(0, 2), ...found.movies.slice(0, 2)]) {
                        try {
                            const mediaType = item.name && !item.title ? "tv" : "movie";
                            const detail = await tmdbhTmdbGet(config, `/${mediaType}/${item.id}`, { append_to_response: "credits" });
                            const record = normalizeTmdbDetail(detail, mediaType);
                            record.imdb = imdbId.toUpperCase();
                            picks.push(record);
                        } catch (err) { /* 单个失败不影响其余候选 */ }
                    }
                    state.candidates = picks;
                    renderBody();
                    if (picks.length) {
                        applyRecordToState(picks[0]);
                        toast(`IMDb 反查到 ${picks.length} 个条目，已载入第一个`, "ok");
                    } else {
                        throw new Error("TMDB 没有找到该 IMDb 编号对应的条目");
                    }
                } else if (tab === "text") {
                    const textarea = bodyEl.querySelector('[data-role="entry-text"]');
                    const parsed = parseEntryText(textarea ? textarea.value : "");
                    if (!parsed.title) throw new Error("没有解析出标题，请检查文本格式");
                    applyRecordToState(normalizeParsedEntry(parsed));
                    toast(`已解析：${parsed.title}`, "ok");
                }
            } catch (err) {
                state.recordError = err.message;
                renderBody();
                // 搜索失败必须可感知：右侧已有旧条目时 recordHeader 不显示错误，这里补 toast
                toast(`查询失败：${err.message}`, "err");
            } finally {
                setLoading(setLoadingKey, false);
            }
        }

        async function pickCandidate(index) {
            const candidate = state.candidates[Number(index)];
            if (!candidate) return;
            const config = configStore.get();
            setLoading("candidate-load", true);
            try {
                let record = candidate;
                if (candidate.source === "douban" && candidate.sourceId) {
                    if (!candidate.overview) {
                        const detail = await fetchDoubanDetail(candidate.sourceId, config);
                        record = normalizeDoubanDetail(detail);
                    }
                } else if (candidate.source === "tmdb" && candidate.sourceId && !candidate.overview && !/^(season|episode):/.test(candidate.sourceId)) {
                    if (tmdbhTmdbReady(config)) {
                        const mediaType = /\/tv\//.test(candidate.url || "") ? "tv" : "movie";
                        const detail = await tmdbhTmdbGet(config, `/${mediaType}/${Number(candidate.sourceId)}`, { append_to_response: "credits" });
                        record = normalizeTmdbDetail(detail, mediaType);
                    }
                } else if (!["douban", "tmdb", "imdb", "text"].includes(candidate.source)) {
                    const adapter = sources.get(candidate.source);
                    // alwaysLoadDetail：候选只是搜索页的浅摘要（如百科），点卡片必须再拉完整详情
                    if (adapter && typeof adapter.findById === "function" && (!candidate.overview || adapter.alwaysLoadDetail)) {
                        // candidate 一并传入：部分数据源（如百科）要从候选的 url 定位详情
                        record = createUnifiedRecord(await adapter.findById(candidate.sourceId, { config, candidate }));
                    }
                }
                if (candidate.imdb && !record.imdb) record.imdb = candidate.imdb;
                state.candidates = state.candidates.map((item, i) => (i === Number(index) ? record : item));
                applyRecordToState(record);
                toast(`已载入：${record.title}`, "ok");
            } catch (err) {
                state.recordError = err.message;
                renderBody();
            } finally {
                setLoading("candidate-load", false);
            }
        }

        // —— 对照写入 ——
        function syncEpisodeScheduleFromDom() {
            const read = (name) => {
                const el = bodyEl.querySelector(`[data-gen="${name}"]`);
                return el ? el.value : undefined;
            };
            const patch = {
                pattern: read("pattern"),
                startNumber: read("startNumber"),
                count: read("count"),
                perSlot: read("perSlot"),
                startDate: read("startDate"),
                weekdays: read("weekdays"),
                intervalDays: read("intervalDays"),
                titleTemplate: read("titleTemplate"),
                runtime: read("runtime")
            };
            for (const key of Object.keys(patch)) {
                if (patch[key] !== undefined) state.schedule[key] = patch[key];
            }
        }

        function effectiveSeason() {
            return Number.isFinite(state.seasonOverride) ? state.seasonOverride : pageContext.seasonNumber;
        }

        async function refreshExistingEpisodes() {
            const season = effectiveSeason();
            const doneSet = loadDoneEpisodes(storage, pageContext.tvId, season);
            try {
                const data = await fetchRemoteEpisodesData(pageContext.tvId, season);
                const existing = normalizeRemoteEpisodes(data);
                state.existingIndex = buildRemoteEpisodeIndex(data);
                state.existingNumbers = new Set([...existing.map((ep) => ep.episodeNumber), ...doneSet]);
                return true;
            } catch (err) {
                state.existingIndex = {};
                state.existingNumbers = new Set(doneSet);
                toast(`已有单集读取失败：${err.message}`, "err");
                return false;
            } finally {
                const numbers = Array.from(state.existingNumbers);
                state.nextEpisode = numbers.length ? Math.max(...numbers) + 1 : 1;
            }
        }

        function resetEpisodeSelections() {
            state.duplicateNumbers = new Set(findDuplicateEpisodeNumbers(state.episodes));
            const seen = new Set();
            state.epSelected = new Map();
            for (const ep of state.episodes) {
                const isDup = seen.has(ep.episodeNumber);
                seen.add(ep.episodeNumber);
                state.epSelected.set(ep.episodeNumber, !isDup && !state.existingNumbers.has(ep.episodeNumber));
            }
        }

        function resetEpisodeProgress() {
            state.epStatus = new Map();
            state.progress = 0;
            state.progressText = "";
            state.finished = false;
        }

        // 粘贴列表统一管线：解析 → 过滤词剔除 + 重编号（TMDB-Import 同款）
        function parseEpisodeListWithFilters(text) {
            const filtered = applyEpisodeFilterWords(parseEpisodeText(text), configStore.get().filters.words);
            return filtered;
        }

        function filterToastSuffix(filtered) {
            if (!filtered.removed.length) return "";
            const preview = filtered.removed.slice(0, 3).map((ep) => `${ep.name || `#${ep.episodeNumber}`}`).join("、");
            return `，过滤词剔除 ${filtered.removed.length} 行（${preview}${filtered.removed.length > 3 ? "…" : ""}），其余已重编号`;
        }

        // 平台抓取共用落表：过滤词 → 整表替换 → 回填解析框 → 记录剧集信息卡（海报/简介）→ 刷新
        async function applySiteEpisodes(platformName, result) {
            const rows = Array.isArray(result.episodes) ? result.episodes.slice(0, 500) : [];
            if (!rows.length) throw new Error(`${platformName} 没有抓到分集（可能需要登录，或页面结构已变化）`);
            const filtered = applyEpisodeFilterWords(rows, configStore.get().filters.words);
            state.episodes = filtered.episodes;
            state.episodeText = exportEpisodesToTsv(rows);
            const textarea = bodyEl.querySelector('[data-role="episode-text"]');
            if (textarea) textarea.value = state.episodeText;
            state.siteMeta = {
                platform: platformName,
                title: String(result.title || "").trim(),
                overview: String(result.overview || "").trim(),
                cover: String(result.cover || "").trim(),
                episodeCnt: Number(result.episodeCnt) || rows.length
            };
            state.stillStatus = new Map();
            resetEpisodeProgress();
            await refreshExistingEpisodes();
            resetEpisodeSelections();
            renderBody();
            schedulePersistPanelState();
            toast(`${platformName} 抓到 ${rows.length} 集${result.title ? `《${result.title}》` : ""}${filterToastSuffix(filtered)}，已填入表格`, "ok");
        }

        // 平台链接 → 分集列表（TMDB-Import 免浏览器提取器的移植路径）
        async function fetchEpisodesFromSiteUrl(rawUrl) {
            const url = String(rawUrl || "").trim();
            if (!url) throw new Error("请先粘贴平台剧集页链接或剧名");
            const site = matchSiteSource(url);
            if (!site) throw new Error("没有匹配的平台抓取器（支持：哔哩哔哩 / 爱奇艺 / 腾讯视频 / 芒果TV / 优酷 / 红果短剧；输入剧名则搜索红果）");
            const result = await site.fetch(url, { config: configStore.get() });
            await applySiteEpisodes(site.name, result);
        }

        // 剧名搜索：红果短剧 + 哔哩哔哩 并行（两家有公开搜索接口；其余平台搜索封闭仍走链接抓取）
        async function searchSiteSources(keyword) {
            const [hongguo, bili] = await Promise.allSettled([
                (async () => {
                    const html = await tmdbhSiteGetText(`https://hongguoduanju.com/search/${encodeURIComponent(keyword)}`);
                    const data = extractHongguoRouterData(html);
                    const loaderData = (data && data.loaderData) || {};
                    const searchPage = loaderData["search_(keyword)/page"] || loaderData.search_page || {};
                    return mapHongguoSearchList(searchPage.searchList);
                })(),
                (async () => {
                    const data = await tmdbhBiliSearchJSON(keyword);
                    return mapBilibiliSearchResults(data);
                })()
            ]);
            const candidates = [];
            if (hongguo.status === "fulfilled") candidates.push(...hongguo.value);
            if (bili.status === "fulfilled") candidates.push(...bili.value);
            state.siteCandidates = candidates.slice(0, 10);
            state.siteMeta = null;
            renderBody();
            schedulePersistPanelState();
            if (candidates.length) {
                const parts = [];
                if (hongguo.status === "fulfilled") parts.push(`红果 ${hongguo.value.length}`);
                if (bili.status === "fulfilled") parts.push(`B站 ${bili.value.length}`);
                toast(`「${keyword}」搜索结果：${parts.join("，")}，点击候选抓取分集`, "ok");
            } else {
                toast(`「${keyword}」没有搜到相关剧集`, "err");
            }
        }

        async function fetchHongguoSeriesById(seriesId) {
            const html = await tmdbhSiteGetText(`https://hongguoduanju.com/detail?series_id=${seriesId}`);
            await applySiteEpisodes("红果短剧", parseHongguoSeries(html));
        }

        // 平台抓到的封面一键直传为季海报：同源抓官方季海报页拿上传配置，下载封面 → 规范处理 → POST /image
        async function uploadSiteCoverAsSeasonPoster() {
            const meta = state.siteMeta;
            if (!meta || !meta.cover) {
                toast("信息卡里没有封面可上传", "err");
                return;
            }
            setLoading("cover-upload", true);
            try {
                const season = effectiveSeason();
                const pageHtml = await fetch(`/tv/${Number(pageContext.tvId)}/season/${Number(season)}/images/posters`, { credentials: "same-origin" }).then((r) => r.text());
                const config = parseImageUploadConfig(pageHtml);
                if (!config || config.mediaType !== "TvSeason") throw new Error("季海报页没有解析到上传配置（请确认官方页面处于登录状态）");
                const response = await tmdbhGmRequest({
                    method: "GET",
                    url: meta.cover,
                    headers: { Referer: tmdbhImageReferer(meta.cover) || location.origin, "Accept": "image/avif,image/webp,image/*,*/*" },
                    responseType: "blob",
                    timeout: 30000
                });
                if (response.status >= 400) throw new Error(`封面下载失败（HTTP ${response.status}）`);
                const blob = response.response;
                if (!blob || typeof blob !== "object") throw new Error("封面下载结果为空");
                const ext = (blob.type && blob.type.split("/")[1] || "jpg").replace(/[^a-z0-9]/gi, "") || "jpg";
                const file = new File([blob], `site-cover.${ext}`, { type: blob.type || "image/jpeg" });
                const prepared = await prepareImageForUpload(file, "poster");
                await uploadPosterFile(prepared.file, config);
                toast(`季海报已直传（${prepared.width}×${prepared.height}${prepared.notes.length ? `，${prepared.notes[0]}` : ""}），官方季图片页刷新可见`, "ok");
            } catch (err) {
                toast(`上传失败：${err.message}`, "err");
            } finally {
                setLoading("cover-upload", false);
            }
        }

        // —— 一键上传海报（搜索浮层 → 当前条目）：任意页面上把来源条目的海报直传到 TMDB ——
        // 当前页面能定位到哪个条目，就传到哪个条目的海报库；图片上传页则直接用本页配置
        // （单集页只收 16:9 剧照，不提供海报上传）
        function canUploadPosterHere() {
            if (["images", "season-images"].includes(pageContext.kind)) return true;
            return Boolean(posterUploadPageUrl());
        }

        function posterUploadPageUrl() {
            const kind = pageContext.kind;
            if ((kind === "movie-detail" || kind === "movie-edit") && pageContext.id) return `/movie/${Number(pageContext.id)}/images/posters`;
            // tv-detail / tv-edit 的上下文字段是 id（tvId 只在 season/episode 系页面才有）
            if ((kind === "tv-detail" || kind === "tv-edit") && pageContext.id) return `/tv/${Number(pageContext.id)}/images/posters`;
            if (kind === "episode-edit" && pageContext.tvId) return `/tv/${Number(pageContext.tvId)}/images/posters`;
            if ((kind === "season-edit" || kind === "season-detail") && pageContext.tvId) return seasonImagesUrl(pageContext.tvId, effectiveSeason());
            return "";
        }

        // 同源抓官方图片页拿内嵌上传配置（media_id/media_type），按页面缓存；图片上传页直接读本页配置
        const posterTargetCache = new Map();
        async function resolvePosterUploadTarget() {
            if (["images", "season-images"].includes(pageContext.kind)) {
                const config = readUploadConfig();
                if (!config) throw new Error("本页没有解析到上传配置（请确认已登录 TMDB）");
                return config;
            }
            if (pageContext.kind === "episode-images") throw new Error("单集只支持剧照（16:9），请改用「上传图片」面板直传");
            const url = posterUploadPageUrl();
            if (!url) throw new Error("当前页面识别不到 TMDB 条目，请到条目页/编辑页/图片页再试");
            if (!posterTargetCache.has(url)) {
                const html = await fetch(url, { credentials: "same-origin" }).then((response) => {
                    if (!response.ok) throw new Error(`读取官方图片页失败（HTTP ${response.status}，请确认已登录 TMDB）`);
                    return response.text();
                });
                posterTargetCache.set(url, parseImageUploadConfig(html));
            }
            const config = posterTargetCache.get(url);
            if (!config) throw new Error("官方图片页没有解析到上传配置（请确认已登录 TMDB）");
            return config;
        }

        async function uploadRecordPoster() {
            const rec = state.record;
            if (!rec || !rec.poster) {
                toast("该条目没有海报可上传", "err");
                return;
            }
            setLoading("record-poster-upload", true);
            try {
                const target = await resolvePosterUploadTarget();
                // 本功能只传海报：media_id/media_type 用页面配置，type 固定 poster
                // （背景图页等复用同一 media_id，传海报同样有效）
                const posterTarget = { mediaId: target.mediaId, mediaType: target.mediaType, type: "poster" };
                // 豆瓣/百科小图升级原图、TMDB 小图变体升级 original，否则过不了 TMDB 最低分辨率
                const rawUrl = upgradeBaikePosterUrl(String(rec.poster || "").trim());
                const url = upgradeDoubanPosterUrl(rawUrl).replace(/(image\.tmdb\.org\/t\/p\/)(?:w\d+|h\d+|original)\//, "$1original/");
                const response = await tmdbhGmRequest({
                    method: "GET",
                    url,
                    headers: { Referer: tmdbhImageReferer(url) || location.origin, "Accept": "image/avif,image/webp,image/*,*/*" },
                    responseType: "blob",
                    timeout: 30000
                });
                if (response.status >= 400) throw new Error(`海报下载失败（HTTP ${response.status}）`);
                const blob = response.response;
                if (!blob || typeof blob !== "object") throw new Error("海报下载结果为空");
                const ext = (blob.type && blob.type.split("/")[1] || "jpg").replace(/[^a-z0-9]/gi, "") || "jpg";
                const file = new File([blob], `record-poster.${ext}`, { type: blob.type || "image/jpeg" });
                const prepared = await prepareImageForUpload(file, "poster");
                const data = await uploadPosterFile(prepared.file, posterTarget);
                const mounted = data && data.html && ["images", "season-images"].includes(pageContext.kind)
                    ? mountUploadedImageCard(data.html, posterTarget)
                    : false;
                state.posterUploaded = true;
                const notesText = prepared.notes.length ? `（${prepared.notes.join("；")}）` : "";
                toast(`海报已直传到当前条目${notesText}，输出 ${prepared.width}×${prepared.height}。${mounted ? "已显示在页面画廊顶部" : "官方图片页刷新可见"}`, "ok");
            } catch (err) {
                toast(`上传海报失败：${err.message}`, "err");
            } finally {
                setLoading("record-poster-upload", false);
            }
        }

        // —— 百科分集剧情：抓取 → 展示 → 复制 TSV / 填入季编辑器分集表格 ——
        async function fetchRecordBaikeEpisodes() {
            const rec = state.record;
            if (!rec || rec.source !== "baike" || !rec.url) {
                toast("请先在「百度百科」来源载入条目", "err");
                return;
            }
            setLoading("baike-episodes", true);
            try {
                const result = await fetchBaikeEpisodes(rec.url, configStore.get());
                state.baikeEpisodes = result.episodes;
                state.baikeEpisodesError = "";
                renderBody();
                toast(`抓到 ${result.episodes.length} 集分集剧情`, "ok");
            } catch (err) {
                state.baikeEpisodes = [];
                state.baikeEpisodesError = String(err.message || err);
                renderBody();
                toast(`抓取分集失败：${err.message}`, "err");
            } finally {
                setLoading("baike-episodes", false);
            }
        }

        function baikeEpisodesTsv() {
            return exportEpisodesToTsv(state.baikeEpisodes);
        }

        async function copyBaikeEpisodesTsv() {
            if (!state.baikeEpisodes.length) {
                toast("还没有已抓取的分集剧情", "err");
                return;
            }
            await tmdbhCopyText(baikeEpisodesTsv());
            toast(`已复制 ${state.baikeEpisodes.length} 集的 TSV`, "ok");
        }

        async function fillEpisodesFromBaike() {
            if (!state.baikeEpisodes.length) {
                toast("还没有已抓取的分集剧情", "err");
                return;
            }
            await applySiteEpisodes("百度百科", { title: state.record && state.record.title || "", episodes: state.baikeEpisodes.slice() });
            state.view = "episodes";
            render();
        }

        // —— 面板状态持久化：搜索到的条目、平台抓取的分集表/信息卡，刷新或切页不丢 ——
        let persistTimer = 0;
        function schedulePersistPanelState() {
            clearTimeout(persistTimer);
            persistTimer = setTimeout(persistPanelStateNow, 600);
        }
        function persistPanelStateNow() {
            try {
                storage.set("Tmdb.Helper.PanelState", JSON.stringify({
                    savedAt: Date.now(),
                    kind: pageContext.kind,
                    tvId: pageContext.tvId || null,
                    season: effectiveSeason(),
                    record: state.record,
                    candidates: state.candidates,
                    sourceTab: state.sourceTab,
                    searchQuery: state.searchQuery,
                    entryTextDraft: state.entryTextDraft,
                    siteUrlQuery: state.siteUrlQuery,
                    siteMeta: state.siteMeta,
                    siteCandidates: state.siteCandidates,
                    baikeEpisodes: state.baikeEpisodes,
                    episodes: state.episodes,
                    episodeText: state.episodeText
                }));
            } catch (err) { /* 存储失败仅在内存生效 */ }
        }
        function restorePersistedPanelState() {
            let saved = null;
            try {
                saved = JSON.parse(storage.get("Tmdb.Helper.PanelState") || "null");
            } catch (err) {
                saved = null;
            }
            if (!saved) return;
            // 搜索视图状态：同一条目页（同类型同 ID）才恢复，避免跨条目串台
            const sameEntry = saved.kind === pageContext.kind && (saved.tvId || null) === (pageContext.tvId || null)
                && (saved.record || Array.isArray(saved.candidates) && saved.candidates.length);
            if (sameEntry) {
                if (saved.record) state.record = saved.record;
                if (Array.isArray(saved.candidates)) state.candidates = saved.candidates;
                if (saved.sourceTab && state.searchQuery[saved.sourceTab] !== undefined) state.sourceTab = saved.sourceTab;
                if (saved.searchQuery) state.searchQuery = Object.assign(state.searchQuery, saved.searchQuery);
                if (saved.entryTextDraft) state.entryTextDraft = saved.entryTextDraft;
                // 百科分集剧情跟来源条目走：仅当恢复的条目还是百科条目时才还原
                if (Array.isArray(saved.baikeEpisodes) && saved.baikeEpisodes.length && state.record && state.record.source === "baike") {
                    state.baikeEpisodes = saved.baikeEpisodes;
                }
            }
            // 平台抓取状态：同剧同季才恢复
            const sameSeason = saved.tvId != null && saved.tvId === pageContext.tvId && Number(saved.season) === Number(pageContext.seasonNumber);
            if (sameSeason) {
                if (Array.isArray(saved.episodes) && saved.episodes.length) state.episodes = saved.episodes;
                if (saved.episodeText) state.episodeText = saved.episodeText;
                if (saved.siteMeta) state.siteMeta = saved.siteMeta;
                if (Array.isArray(saved.siteCandidates)) state.siteCandidates = saved.siteCandidates;
                if (saved.siteUrlQuery) state.siteUrlQuery = saved.siteUrlQuery;
            }
        }

        // tmdb-scraper 同款：拉官方季分集，按集号合并进表格——已有行只补空字段，不覆盖手填内容
        async function pullSeasonFromTmdb() {
            const config = configStore.get();
            if (!tmdbhTmdbReady(config)) {
                toast("拉取需要 TMDB API Key：到「IMDb」来源页签保存后重试", "err");
                return;
            }
            setLoading("ep-pull-tmdb", true);
            try {
                const season = effectiveSeason();
                const data = await tmdbhTmdbGet(config, `/tv/${Number(pageContext.tvId)}/season/${Number(season)}`);
                const pulled = mapTmdbSeasonEpisodes(data);
                if (!pulled.length) {
                    toast(`TMDB 第 ${season} 季没有分集数据`, "err");
                    return;
                }
                const byNumber = new Map(state.episodes.map((ep) => [ep.episodeNumber, ep]));
                let added = 0;
                let filled = 0;
                for (const item of pulled) {
                    const current = byNumber.get(item.episodeNumber);
                    if (!current) {
                        byNumber.set(item.episodeNumber, item);
                        added++;
                        continue;
                    }
                    for (const key of ["name", "airDate", "runtime", "overview", "stillUrl"]) {
                        if (!String(current[key] || "").length && item[key]) {
                            current[key] = item[key];
                            filled++;
                        }
                    }
                }
                state.episodes = Array.from(byNumber.values()).sort((a, b) => a.episodeNumber - b.episodeNumber);
                state.stillStatus = new Map();
                resetEpisodeProgress();
                await refreshExistingEpisodes();
                resetEpisodeSelections();
                renderBody();
                toast(`已拉取 TMDB 第 ${season} 季：新增 ${added} 集、补全 ${filled} 个空字段（已有内容未覆盖）`, "ok");
            } catch (err) {
                toast(`拉取失败：${err.message}`, "err");
            } finally {
                setLoading("ep-pull-tmdb", false);
            }
        }

        function updateEpisodeRowUi(episode, statusText, isError) {
            const cell = bodyEl.querySelector(`[data-ep-status="${episode.episodeNumber}"]`);
            if (cell) {
                cell.textContent = statusText;
                const tone = isError ? "err" : statusText === "✓" ? "ok" : "";
                cell.className = `ep-status tmdbh-epstatus ${tone}`;
            }
            const bar = bodyEl.querySelector('[data-role="ep-bar"]');
            if (bar) bar.style.width = `${Math.round(state.progress * 100)}%`;
            const text = bodyEl.querySelector('[data-role="ep-progress-text"]');
            if (text) text.textContent = state.progressText;
        }

        // —— 图片抓取与上传（供上传图片视图使用：直链抓取 → 规范处理 → 直传） ——
        async function loadPoster(urlHint) {
            const input = bodyEl.querySelector('[data-role="poster-url"]');
            const raw = String(urlHint || (input ? input.value.trim() : "") || state.entryPoster || (state.record && state.record.poster) || "");
            // 豆瓣小图变体升级为原图，避免抓到 480×720 过不了 TMDB 最低分辨率
            // TMDB 缩略直链（w342 等小图变体）升级为原图，否则过不了 TMDB 最低分辨率
            const url = upgradeDoubanPosterUrl(raw).replace(/(image\.tmdb\.org\/t\/p\/)(?:w\d+|h\d+|original)\//, "$1original/");
            if (!url) {
                toast("请先粘贴图片直链", "err");
                return;
            }
            state.entryPoster = url;
            setLoading("poster-load", true);
            try {
                const response = await tmdbhGmRequest({
                    method: "GET",
                    url,
                    headers: { "Referer": tmdbhImageReferer(url) || "https://movie.douban.com/", "Accept": "image/avif,image/webp,image/*,*/*" },
                    responseType: "blob",
                    timeout: 30000
                });
                if (response.status >= 400) throw new Error(`图片下载失败（HTTP ${response.status}）`);
                const blob = response.response;
                if (!blob || typeof blob !== "object") throw new Error("图片下载结果为空");
                const ext = (blob.type && blob.type.split("/")[1] || "jpg").replace(/[^a-z0-9]/gi, "") || "jpg";
                const file = new File([blob], `poster.${ext}`, { type: blob.type || "image/jpeg" });
                let dims = "";
                try {
                    const bitmap = await createImageBitmap(blob);
                    dims = `${bitmap.width}×${bitmap.height}`;
                    bitmap.close && bitmap.close();
                } catch (err) { /* 尺寸探测失败不影响主流程 */ }
                if (state.poster.objectUrl) URL.revokeObjectURL(state.poster.objectUrl);
                state.poster = { url, file, objectUrl: URL.createObjectURL(blob), dims };
                renderBody();
                toast(`图片已就绪（${dims ? `${dims}，` : ""}${Math.round(file.size / 1024)} KB），点「直传」上传`, "ok");
            } catch (err) {
                toast(`图片抓取失败：${err.message}`, "err");
            } finally {
                setLoading("poster-load", false);
            }
        }

        // 按 TMDB 官方规范预处理图片（海报 2:3、背景图/剧照 16:9），避免上传后被压变形；
        // options.cropHalf 支持「取左半/右半」（横向拼图封面转单面海报），仅内存处理
        async function prepareImageForUpload(file, typeLabel, options) {
            const spec = TMDBH_IMAGE_SPECS[typeLabel];
            if (!spec) throw new Error(`未知图片类型：${typeLabel}`);
            if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
                throw new Error(`不支持的图片格式：${file.type || "未知"}（仅支持 JPG / PNG / WebP）`);
            }
            const bitmap = await createImageBitmap(file).catch(() => {
                throw new Error("图片解码失败，文件可能已损坏");
            });
            // 黑边裁切（TMDB-Import 同款思路）：剧照/背景图（16:9）默认去上下黑边再按规范裁切；
            // 检测后内容不足原图 25% 视为全暗图误检，放弃裁切。海报/标志不默认启用。
            const wantTrim = typeof (options && options.trimBlackBars) === "boolean"
                ? options.trimBlackBars
                : spec.ratio === 16 / 9;
            let trim = { top: 0, bottom: 0, left: 0, right: 0 };
            if (wantTrim) {
                try {
                    const work = document.createElement("canvas");
                    work.width = bitmap.width;
                    work.height = bitmap.height;
                    const wctx = work.getContext("2d");
                    wctx.drawImage(bitmap, 0, 0);
                    const pixels = wctx.getImageData(0, 0, work.width, work.height);
                    trim = detectImageBlackBars(pixels.data, work.width, work.height);
                    const innerW = bitmap.width - trim.left - trim.right;
                    const innerH = bitmap.height - trim.top - trim.bottom;
                    if (innerW < bitmap.width * 0.25 || innerH < bitmap.height * 0.25) {
                        trim = { top: 0, bottom: 0, left: 0, right: 0 };
                    }
                } catch (err) { /* 像素读取失败（画布被污染等）时跳过去黑边 */ }
            }
            const trimX = trim.left + trim.right;
            const trimY = trim.top + trim.bottom;
            const transform = computeImageTransform(spec, bitmap.width - trimX, bitmap.height - trimY, options);
            if (transform.problems.length) {
                bitmap.close && bitmap.close();
                throw new Error(`${spec.label}不符合 TMDB 规范：${transform.problems.join("；")}`);
            }
            const canvas = document.createElement("canvas");
            canvas.width = transform.width;
            canvas.height = transform.height;
            const ctx = canvas.getContext("2d");
            if (spec.mime === "image/jpeg") {
                // JPEG 不支持透明，先铺白底
                ctx.fillStyle = "#ffffff";
                ctx.fillRect(0, 0, transform.width, transform.height);
            }
            ctx.drawImage(bitmap, trim.left + transform.crop.sx, trim.top + transform.crop.sy, transform.crop.sw, transform.crop.sh, 0, 0, transform.width, transform.height);
            bitmap.close && bitmap.close();
            let blob = await new Promise((resolve) => canvas.toBlob(resolve, spec.mime, 0.92));
            if (!blob) throw new Error("图片处理失败（canvas 导出为空）");
            const notes = transform.notes.slice();
            if (trimX || trimY) {
                notes.push(`已去黑边（上${trim.top} 下${trim.bottom} 左${trim.left} 右${trim.right} 像素）`);
            }
            if (file.type !== spec.mime) {
                notes.push(`已转换为 ${spec.mime === "image/png" ? "PNG" : "JPG"}`);
            }
            if (blob.size > 10 * 1024 * 1024) {
                const smaller = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8));
                if (!smaller || smaller.size > 10 * 1024 * 1024) {
                    throw new Error("处理后的图片仍超过 TMDB 10MB 上传限制，请压缩后重试");
                }
                blob = smaller;
                notes.push("已压缩到 10MB 内");
            }
            const ext = spec.mime === "image/png" ? "png" : "jpg";
            const outFile = new File([blob], `tmdb-${typeLabel}-${transform.width}x${transform.height}.${ext}`, { type: spec.mime });
            return { file: outFile, width: transform.width, height: transform.height, notes };
        }

        // 直传：multipart POST /image（与官方 kendo Upload autoUpload 同契约）
        async function uploadPosterFile(file, config) {
            const formData = new FormData();
            formData.append("upload_files", file, file.name);
            formData.append("media_id", config.mediaId);
            formData.append("media_type", config.mediaType);
            formData.append("type", config.type);
            formData.append("translate", "false");
            const response = await fetch("/image", {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
                credentials: "same-origin",
                body: formData
            });
            const text = await response.text();
            let data = null;
            try { data = JSON.parse(text); } catch (err) {
                // 200 但非 JSON（WAF/登录页）时不能当成功
                throw new Error(`响应异常（HTTP ${response.status}，非 JSON），可能已退出登录`);
            }
            if (response.status === 401 || response.status === 403) throw new Error(`未登录或无权限（HTTP ${response.status}）`);
            if (data && data.failure) {
                const errors = Array.isArray(data.failure.errors) ? data.failure.errors.join("；") : JSON.stringify(data.failure);
                throw new Error(`TMDB 校验失败：${String(errors).slice(0, 200)}`);
            }
            if (!response.ok) throw new Error(`HTTP ${response.status} ${text.slice(0, 120)}`);
            if (!data || data.success !== true) throw new Error("TMDB 未确认上传成功（响应缺少 success 标记）");
            return data;
        }

        // 上传成功后把返回的图片卡片即时插进页面画廊（等价官方 success 回调），免刷新可见
        function mountUploadedImageCard(cardHtml, config) {
            try {
                const doc = new DOMParser().parseFromString(String(cardHtml || ""), "text/html");
                const card = doc.querySelector("li");
                if (!card || !card.id) return false;
                const container = document.querySelector(`div.results[data-media-id="${config.mediaId}"] ul.images`) || document.querySelector("div.results ul.images");
                if (!container) return false;
                const empty = container.querySelector("#no_results");
                if (empty) empty.remove();
                if (container.querySelector(`#${CSS.escape(card.id)}`)) return true; // 已存在（重复上传防护）
                container.prepend(document.importNode(card, true));
                return true;
            } catch (err) {
                return false;
            }
        }

        async function directUploadPoster(typeOverride) {
            const config = readUploadConfig();
            if (!config) {
                toast("本页没有解析到上传配置，请确认在图片上传页", "err");
                return;
            }
            const targetConfig = typeOverride ? Object.assign({}, config, { type: typeOverride }) : config;
            if (!state.poster.file) {
                toast("请先抓取直链图片或选择本地图片", "err");
                return;
            }
            setLoading("upload-direct", true);
            try {
                const cropSelect = bodyEl.querySelector('[data-role="poster-crop"]');
                const cropHalf = cropSelect && ["left", "right"].includes(cropSelect.value) ? cropSelect.value : "";
                const prepared = await prepareImageForUpload(state.poster.file, targetConfig.type, { cropHalf });
                const data = await uploadPosterFile(prepared.file, targetConfig);
                const mounted = data && data.html ? mountUploadedImageCard(data.html, targetConfig) : false;
                state.posterUploaded = true;
                const notesText = prepared.notes.length ? `（${prepared.notes.join("；")}）` : "";
                toast(`图片已按规范上传成功${notesText ? ` ${notesText}` : ""}，输出 ${prepared.width}×${prepared.height}。${mounted ? "已显示在页面画廊顶部" : "点「⟳ 刷新页面」查看"}`, "ok");
                renderBody();
            } catch (err) {
                toast(`直传失败：${err.message}`, "err");
            } finally {
                setLoading("upload-direct", false);
            }
        }

        function pickLocalImage() {
            const temp = document.createElement("input");
            temp.type = "file";
            temp.accept = "image/jpeg,image/png,image/webp";
            temp.style.display = "none";
            (document.body || document.documentElement).appendChild(temp);
            temp.addEventListener("change", () => {
                const file = temp.files && temp.files[0];
                temp.remove();
                if (!file) return;
                state.manualImage = { file, name: file.name, size: file.size };
                renderBody();
                toast(`已选择本地图片：${file.name}`, "ok");
            });
            temp.click();
        }

        async function uploadManualImage() {
            const typeSelect = bodyEl.querySelector("[data-manual-type]");
            const typeLabel = typeSelect ? typeSelect.value : "poster";
            if (!state.manualImage || !state.manualImage.file) {
                toast("请先选择本地图片", "err");
                return;
            }
            const config = readUploadConfig();
            if (!config) {
                toast("本页没有解析到上传配置，请确认在图片上传页使用该功能", "err");
                return;
            }
            setLoading("manual-upload", true);
            try {
                const prepared = await prepareImageForUpload(state.manualImage.file, typeLabel);
                const data = await uploadPosterFile(prepared.file, Object.assign({}, config, { type: typeLabel }));
                const mounted = data && data.html ? mountUploadedImageCard(data.html, config) : false;
                state.posterUploaded = true;
                const notesText = prepared.notes.length ? `（${prepared.notes.join("；")}）` : "";
                toast(`本地图片已按「${TMDBH_IMAGE_SPECS[typeLabel].label}」规范上传成功${notesText ? ` ${notesText}` : ""}，输出 ${prepared.width}×${prepared.height}。${mounted ? "已显示在页面画廊顶部" : "刷新页面可见"}`, "ok");
                renderBody();
            } catch (err) {
                toast(`上传失败：${err.message}`, "err");
            } finally {
                setLoading("manual-upload", false);
            }
        }

        // —— 分集缩略图（剧照）：抓取 → 按官方规范预处理 → 直传 ——
        const stillFiles = new Map(); // episodeNumber → { file, dims, notes }

        async function fetchAndPrepareStills() {
            const targets = state.episodes.filter((ep) => ep.stillUrl && !stillFiles.has(ep.episodeNumber));
            if (!targets.length) {
                toast("表格里没有待抓取的缩略图 URL", "err");
                return;
            }
            setLoading("stills-fetch", true);
            let ok = 0;
            let failed = 0;
            for (const ep of targets) {
                state.stillStatus.set(ep.episodeNumber, "抓取中…");
                updateEpisodeRowUi(ep, episodeStatusText(ep), false);
                try {
                    const response = await tmdbhGmRequest({
                        method: "GET",
                        url: ep.stillUrl,
                        headers: Object.assign({ "Accept": "image/avif,image/webp,image/*,*/*" }, tmdbhImageReferer(ep.stillUrl) ? { "Referer": tmdbhImageReferer(ep.stillUrl) } : {}),
                        responseType: "blob",
                        timeout: 30000
                    });
                    if (response.status >= 400) throw new Error(`HTTP ${response.status}`);
                    const blob = response.response;
                    if (!blob || typeof blob !== "object") throw new Error("下载结果为空");
                    const raw = new File([blob], `still-${ep.episodeNumber}.img`, { type: blob.type || "image/jpeg" });
                    const prepared = await prepareImageForUpload(raw, "still");
                    stillFiles.set(ep.episodeNumber, prepared);
                    state.stillStatus.set(ep.episodeNumber, `✓ ${prepared.width}×${prepared.height}${prepared.notes.length ? `（${prepared.notes[0]}）` : ""}`);
                    ok += 1;
                } catch (err) {
                    state.stillStatus.set(ep.episodeNumber, `✗ ${String(err.message || err).slice(0, 80)}`);
                    failed += 1;
                }
                renderBody();
            }
            setLoading("stills-fetch", false);
            toast(`缩略图预处理完成：成功 ${ok}，失败 ${failed}${failed ? "（低分辨率图片会被拒绝，TMDB 禁止放大）" : ""}`, failed ? "err" : "ok");
        }

        async function uploadEpisodeStill(episodeNumber, prepared, delayMs) {
            const bson = state.existingIndex[episodeNumber] && state.existingIndex[episodeNumber].bson_id;
            if (!bson) throw new Error("该集还没有 bson_id：请先提交分集（或稍后重试，索引会自动刷新）");
            const fields = buildStillUploadFields(bson);
            const formData = new FormData();
            formData.append("upload_files", prepared.file, prepared.file.name);
            for (const [key, value] of Object.entries(fields)) formData.append(key, value);
            const response = await fetch("/image", {
                method: "POST",
                headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
                credentials: "same-origin",
                body: formData
            });
            const text = await response.text();
            const result = parseEpisodeMutationResult(response, text);
            if (result && result.success !== true && !result.id && !result.failure) {
                /* 官方成功响应带 success:true；其余情况交由 parseEpisodeMutationResult 把关 */
            }
            return result;
        }

        async function submitStills() {
            const targets = state.episodes.filter((ep) => stillFiles.has(ep.episodeNumber));
            if (!targets.length) {
                toast("还没有已就绪的缩略图，请先「抓取并预处理」", "err");
                return;
            }
            setLoading("stills-submit", true);
            const cfg = configStore.get();
            try {
                await refreshExistingEpisodes();
            } catch (err) { /* 索引刷新失败时逐集报错 */ }
            let ok = 0;
            let failed = 0;
            for (const ep of targets) {
                state.stillStatus.set(ep.episodeNumber, "上传中…");
                renderBody();
                try {
                    await uploadEpisodeStill(ep.episodeNumber, stillFiles.get(ep.episodeNumber), cfg.stills.delayMs);
                    state.stillStatus.set(ep.episodeNumber, "✓ 已上传");
                    ok += 1;
                } catch (err) {
                    const message = err && err.auth ? `${err.message}（请先登录 TMDB）` : String(err.message || err);
                    state.stillStatus.set(ep.episodeNumber, `✗ ${message.slice(0, 80)}`);
                    failed += 1;
                    if (err && err.auth) break;
                }
                await tmdbhSleep(Math.max(200, cfg.stills.delayMs));
            }
            setLoading("stills-submit", false);
            toast(`缩略图上传完成：成功 ${ok}，失败 ${failed}${ok ? "；可点「官方剧照页示例」核对" : ""}`, failed ? "err" : "ok");
        }

        // —— 批量操作扩展环境 ——
        function episodeActionEnv() {
            return {
                tvId: pageContext.tvId,
                seasonNumber: effectiveSeason(),
                language: state.language || tmdbhDetectEditorLanguage(),
                episodes: state.episodes,
                selectedNumbers: state.episodes.filter((ep) => state.epSelected.get(ep.episodeNumber) === true).map((ep) => ep.episodeNumber),
                existingIndex: state.existingIndex,
                config: configStore.get(),
                toast,
                refreshExisting: refreshExistingEpisodes,
                fetchAndPrepareStills,
                submitStills
            };
        }


        // —— 剧集组（内部接口优先，公开 API 兜底） ——
        async function loadGroups() {
            const tvId = pageContext.tvId || pageContext.id;
            if (!tvId) return;
            setLoading("groups-load", true);
            state.groupsError = "";
            try {
                try {
                    state.groups = await fetchGroupsInternal(tvId);
                } catch (err) {
                    const cfg = configStore.get();
                    if (!tmdbhTmdbReady(cfg)) throw err;
                    state.groups = await tmdbhTmdbEpisodeGroups(cfg, tvId);
                }
                toast(state.groups.length ? `读取到 ${state.groups.length} 个剧集组` : "该剧还没有剧集组", "ok");
            } catch (err) {
                state.groups = [];
                state.groupsError = `剧集组读取失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`;
            } finally {
                setLoading("groups-load", false);
            }
        }

        async function loadGroupDetails(groupId) {
            if (!groupId) return;
            const tvId = pageContext.tvId || pageContext.id;
            setLoading("group-details", true);
            try {
                let details;
                try {
                    const subGroups = await fetchSubGroupsInternal(tvId, groupId);
                    const current = state.groups.find((group) => String(group.id) === String(groupId)) || {};
                    details = { name: current.name || "", description: current.description || "", type: current.type, groups: subGroups, internal: true };
                } catch (err) {
                    const cfg = configStore.get();
                    if (!tmdbhTmdbReady(cfg)) throw err;
                    details = await tmdbhTmdbEpisodeGroupDetails(cfg, groupId);
                    details.internal = false;
                }
                state.groupDetails = Object.assign({}, state.groupDetails, { [groupId]: details });
                renderBody();
            } catch (err) {
                toast(`剧集组结构读取失败：${err.message}${err.auth ? "（请先登录 TMDB，或填写 API Key）" : ""}`, "err");
            } finally {
                setLoading("group-details", false);
            }
        }

        async function loadSubGroupEpisodes(groupId, subGroupId) {
            const tvId = pageContext.tvId || pageContext.id;
            setLoading(`subgroup-eps-${subGroupId}`, true);
            try {
                const episodes = await fetchSubGroupEpisodesInternal(tvId, groupId, subGroupId);
                state.subGroupEpisodes = Object.assign({}, state.subGroupEpisodes, { [subGroupId]: episodes });
                renderBody();
            } catch (err) {
                toast(`子组单集读取失败：${err.message}`, "err");
            } finally {
                setLoading(`subgroup-eps-${subGroupId}`, false);
            }
        }

        async function createGroupDirect() {
            const tvId = pageContext.tvId || pageContext.id;
            if (!tvId) return;
            const read = (name) => {
                const el = bodyEl.querySelector(`[data-group-name="${name}"]`);
                return el ? (name === "type" ? Number(el.value) : String(el.value || "").trim()) : "";
            };
            const name = read("name");
            const desc = read("desc");
            const type = read("type") || 2;
            if (!name) {
                toast("请先填写组名称", "err");
                return;
            }
            if (!window.confirm(`确认在 TMDB 上创建剧集组「${name}」？（走官网同款接口，创建后立即生效）`)) return;
            setLoading("group-create", true);
            try {
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupsRemoteUrl(tvId), "POST", buildEpisodeGroupWritePayload({ name, description: desc, type }, false)));
                toast(`剧集组「${name}」已创建，刷新列表可见`, "ok");
                state.groupDraftName = "";
                state.groupDraftDesc = "";
                await loadGroups();
            } catch (err) {
                toast(`创建失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`, "err");
            } finally {
                setLoading("group-create", false);
            }
        }

        async function editGroup(group) {
            const tvId = pageContext.tvId || pageContext.id;
            const name = window.prompt("修改组名称：", group.name || "");
            if (name === null) return;
            const description = window.prompt("修改描述（可留空）：", group.description || "");
            if (description === null) return;
            const typeRaw = window.prompt("类型编号（1 首播顺序 / 2 绝对顺序 / 3 DVD / 4 数字 / 5 故事线 / 6 制作顺序）：", String(group.type || 2));
            if (typeRaw === null) return;
            setLoading(`group-edit-${group.id}`, true);
            try {
                const payload = buildEpisodeGroupWritePayload({ name: name.trim() || group.name, description, type: Number(typeRaw), id: group.id }, true);
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupsRemoteUrl(tvId), "PUT", payload));
                toast("剧集组已更新", "ok");
                await loadGroups();
            } catch (err) {
                toast(`更新失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`, "err");
            } finally {
                setLoading(`group-edit-${group.id}`, false);
            }
        }

        async function deleteGroup(group) {
            const tvId = pageContext.tvId || pageContext.id;
            if (!window.confirm(`确认删除剧集组「${group.name || group.id}」？此操作不可恢复！`)) return;
            if (!window.confirm("再次确认：真的要删除吗？")) return;
            setLoading(`group-del-${group.id}`, true);
            try {
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupsRemoteUrl(tvId), "DELETE", { id: String(group.id) }));
                toast("剧集组已删除", "ok");
                await loadGroups();
            } catch (err) {
                toast(`删除失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`, "err");
            } finally {
                setLoading(`group-del-${group.id}`, false);
            }
        }

        async function createSubGroup(groupId) {
            const tvId = pageContext.tvId || pageContext.id;
            const name = window.prompt("新子组名称（例如「第 1 碟」「Part 1」）：", "");
            if (name === null || !name.trim()) return;
            const details = state.groupDetails[groupId];
            const order = Array.isArray(details && details.groups) ? details.groups.length : 0;
            setLoading(`subgroup-add-${groupId}`, true);
            try {
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupSubGroupsUrl(tvId, groupId), "POST", buildSubGroupWritePayload({ name: name.trim() }, false, order)));
                toast(`子组「${name.trim()}」已创建`, "ok");
                await loadGroupDetails(groupId);
            } catch (err) {
                toast(`子组创建失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`, "err");
            } finally {
                setLoading(`subgroup-add-${groupId}`, false);
            }
        }

        async function editSubGroup(groupId, sub) {
            const tvId = pageContext.tvId || pageContext.id;
            const name = window.prompt("修改子组名称：", sub.name || "");
            if (name === null || !name.trim()) return;
            const orderRaw = window.prompt("排序号（数字，越小越靠前）：", String(sub.order ?? 0));
            if (orderRaw === null) return;
            setLoading(`subgroup-edit-${sub.id}`, true);
            try {
                const payload = buildSubGroupWritePayload({ name: name.trim(), order: Number(orderRaw), episode_count: sub.episode_count, id: sub.id }, true, sub.order);
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupSubGroupsUrl(tvId, groupId), "PUT", payload));
                toast("子组已更新", "ok");
                await loadGroupDetails(groupId);
            } catch (err) {
                toast(`子组更新失败：${err.message}`, "err");
            } finally {
                setLoading(`subgroup-edit-${sub.id}`, false);
            }
        }

        async function deleteSubGroup(groupId, sub) {
            const tvId = pageContext.tvId || pageContext.id;
            if (!window.confirm(`确认删除子组「${sub.name || sub.id}」？（组内单集映射会一并解除）`)) return;
            setLoading(`subgroup-del-${sub.id}`, true);
            try {
                await checkGroupMutation(await tmdbhInternalJson(episodeGroupSubGroupsUrl(tvId, groupId), "DELETE", { id: String(sub.id) }));
                toast("子组已删除", "ok");
                if (state.subGroupPicker && state.subGroupPicker.subGroupId === sub.id) state.subGroupPicker = null;
                await loadGroupDetails(groupId);
            } catch (err) {
                toast(`子组删除失败：${err.message}`, "err");
            } finally {
                setLoading(`subgroup-del-${sub.id}`, false);
            }
        }

        function openSubGroupPicker(groupId, subGroupId) {
            state.subGroupPicker = { groupId, subGroupId, season: "", list: null, selected: new Set() };
            renderBody();
        }

        async function loadPickerSeason(groupId, subGroupId) {
            const tvId = pageContext.tvId || pageContext.id;
            const seasonInput = bodyEl.querySelector("[data-picker-season]");
            const season = seasonInput ? Math.round(Number(seasonInput.value)) : 0;
            if (!Number.isFinite(season) || season < 0) {
                toast("请输入有效的季号", "err");
                return;
            }
            state.subGroupPicker = { groupId, subGroupId, season, list: null, selected: new Set() };
            setLoading("picker-load", true);
            try {
                const data = await fetchRemoteEpisodesData(tvId, season);
                const raw = buildRemoteEpisodeIndex(data);
                const existing = state.subGroupEpisodes[subGroupId] || [];
                const existingKeys = existing.map((ep) => String(ep.media_id || ""));
                state.subGroupPicker.list = filterSubGroupCandidates(Object.values(raw), existingKeys);
                if (!state.subGroupPicker.list.length) toast("该季单集都已在子组里", "ok");
            } catch (err) {
                toast(`该季单集读取失败：${err.message}`, "err");
                state.subGroupPicker.list = [];
            } finally {
                setLoading("picker-load", false);
            }
        }

        async function addPickedEpisodes(groupId, subGroupId) {
            const tvId = pageContext.tvId || pageContext.id;
            const picker = state.subGroupPicker;
            if (!picker || !picker.list || !picker.selected.size) {
                toast("请先勾选要添加的单集", "err");
                return;
            }
            setLoading("picker-add", true);
            try {
                const existing = (state.subGroupEpisodes[subGroupId] || []).map((ep, index) => ({
                    media_id: ep.media_id,
                    season_number: ep.season_number,
                    episode_number: ep.episode_number,
                    name: ep.name || "",
                    order: index
                }));
                const additions = picker.list
                    .filter((ep) => picker.selected.has(String(ep.bson_id || ep.media_id || "")))
                    .map((ep, index) => ({
                        media_id: String(ep.bson_id || ep.media_id || ""),
                        season_number: Number(ep.season_number) || 0,
                        episode_number: Number(ep.episode_number) || 0,
                        name: String(ep.name || ""),
                        order: existing.length + index
                    }));
                await saveSubGroupEpisodes(tvId, groupId, subGroupId, existing.concat(additions));
                toast(`已添加 ${additions.length} 集到子组`, "ok");
                state.subGroupPicker = null;
                await loadSubGroupEpisodes(groupId, subGroupId);
            } catch (err) {
                toast(`添加失败：${err.message}${err.auth ? "（请先登录 TMDB）" : ""}`, "err");
            } finally {
                setLoading("picker-add", false);
            }
        }

        async function sortSubGroupEpisodesAction(groupId, subGroupId) {
            const tvId = pageContext.tvId || pageContext.id;
            const episodes = state.subGroupEpisodes[subGroupId];
            if (!Array.isArray(episodes) || !episodes.length) {
                toast("请先点「单集」载入该子组的集列表", "err");
                return;
            }
            setLoading(`subgroup-sort-${subGroupId}`, true);
            try {
                await saveSubGroupEpisodes(tvId, groupId, subGroupId, sortSubGroupEpisodes(episodes));
                toast("已按季/集号升序重排并保存", "ok");
                await loadSubGroupEpisodes(groupId, subGroupId);
            } catch (err) {
                toast(`重排失败：${err.message}`, "err");
            } finally {
                setLoading(`subgroup-sort-${subGroupId}`, false);
            }
        }

        async function removeSubGroupEpisodeAction(groupId, subGroupId, mediaId) {
            const tvId = pageContext.tvId || pageContext.id;
            const episodes = state.subGroupEpisodes[subGroupId] || [];
            const item = episodes.find((ep) => String(ep.media_id) === String(mediaId));
            if (!item) {
                toast("未找到该集，请重新载入子组单集", "err");
                return;
            }
            if (!window.confirm(`确认把 S${item.season_number}E${item.episode_number}「${item.name || ""}」移出子组？`)) return;
            setLoading(`subgroup-ep-del-${subGroupId}-${mediaId}`, true);
            try {
                await removeSubGroupEpisode(tvId, groupId, subGroupId, item);
                toast("已移出子组", "ok");
                await loadSubGroupEpisodes(groupId, subGroupId);
            } catch (err) {
                toast(`移除失败：${err.message}`, "err");
            } finally {
                setLoading(`subgroup-ep-del-${subGroupId}-${mediaId}`, false);
            }
        }

        function makeDraggable(handle, getTarget, onStart, onEnd) {
            let drag = null;
            handle.addEventListener("mousedown", (event) => {
                if (event.target.closest("button, a, input, select, textarea")) return;
                const target = getTarget();
                drag = {
                    startX: event.clientX,
                    startY: event.clientY,
                    originX: target.offsetLeft,
                    originY: target.offsetTop,
                    moved: false
                };
                event.preventDefault();
            });
            window.addEventListener("mousemove", (event) => {
                if (!drag) return;
                const target = getTarget();
                const dx = event.clientX - drag.startX;
                const dy = event.clientY - drag.startY;
                if (!drag.moved && Math.abs(dx) + Math.abs(dy) <= 3) return;
                drag.moved = true;
                if (onStart && !drag.started) {
                    drag.started = true;
                    onStart();
                }
                const maxX = Math.max(4, window.innerWidth - target.offsetWidth - 4);
                const maxY = Math.max(4, window.innerHeight - target.offsetHeight - 4);
                target.style.left = `${Math.max(4, Math.min(maxX, drag.originX + dx))}px`;
                target.style.top = `${Math.max(4, Math.min(maxY, drag.originY + dy))}px`;
            });
            window.addEventListener("mouseup", () => {
                if (drag && drag.moved && onEnd) onEnd();
                drag = null;
            });
        }

        let suppressBallClick = false;
        makeDraggable(
            ball,
            () => ball,
            null,
            () => {
                suppressBallClick = true;
                configStore.update({ panel: { x: ball.offsetLeft, y: ball.offsetTop } });
            }
        );
        ball.addEventListener("click", () => {
            if (suppressBallClick) {
                suppressBallClick = false;
                return;
            }
            setOpen(!overlayEl.classList.contains("open"));
        });
        // 右键悬浮球：不打开浮层，直接复制「名称 (年份) {tmdbid=…}」（123 助手同款标记格式）
        ball.addEventListener("contextmenu", async (event) => {
            event.preventDefault();
            const id = pageContext.id || pageContext.tvId;
            if (!id) {
                toast("本页没有条目 ID，无法复制 tmdbid 标记", "err");
                return;
            }
            // 页面标题形如「名称 (TV Series 2026)」，tmdbhParsePageTitle 能把年份剥出来
            const parsed = tmdbhParsePageTitle(document.title);
            const text = `${parsed.title}${parsed.year ? ` (${parsed.year})` : ""} {tmdbid=${id}}`;
            try {
                await tmdbhCopyText(text);
                toast(`已复制：${text}`, "ok");
            } catch (err) {
                toast(`复制失败：${err.message}`, "err");
            }
        });

        // —— 面板宽度拖拽：左缘抓手实时调整停靠台宽度（inline 宽度优先于视图宽版） ——
        const windowEl = shadow.querySelector('[data-role="window"]');
        const resizerEl = shadow.querySelector('[data-role="resizer"]');
        let resize = null;
        resizerEl.addEventListener("mousedown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            resize = { startX: event.clientX, startW: windowEl.offsetWidth };
        });
        window.addEventListener("mousemove", (event) => {
            if (!resize) return;
            const width = Math.max(420, Math.min(window.innerWidth - 16, resize.startW + (resize.startX - event.clientX)));
            windowEl.style.width = `${width}px`;
        });
        window.addEventListener("mouseup", () => { resize = null; });

        // —— 浮层事件委托 ——
        overlayEl.addEventListener("click", async (event) => {
            const actionEl = event.target instanceof Element ? event.target.closest("[data-action]") : null;
            if (!actionEl) return;
            if (actionEl.tagName === "A") return; // 链接走默认跳转
            const action = actionEl.dataset.action;
            try {
                if (action === "close" || action === "collapse") {
                    setOpen(false);
                } else if (action === "toggle") {
                    setOpen(!overlayEl.classList.contains("open"));
                } else if (action === "view") {
                    state.view = actionEl.dataset.view;
                    render();
                } else if (action === "candidate-copy") {
                    const cand = state.candidates[Number(actionEl.dataset.cand)];
                    if (!cand || !cand.title) {
                        toast("该候选没有可复制的名称", "err");
                        return;
                    }
                    const text = recordTitleYear(cand);
                    await tmdbhCopyText(text);
                    toast(`已复制：${text}`, "ok");
                } else if (action === "copy-field") {
                    const rec = state.record;
                    if (!rec || !rec.title) {
                        toast("请先搜索并载入来源条目", "err");
                        return;
                    }
                    const values = unifiedToEntryValues(rec);
                    const key = actionEl.dataset.copy;
                    const textMap = {
                        title: values.title || "",
                        originalTitle: values.originalTitle || "",
                        titleYear: recordTitleYear(rec),
                        originalTitleYear: recordTitleYear(rec, true),
                        date: values.date || "",
                        year: values.year ? String(values.year) : "",
                        overview: values.overview || ""
                    };
                    const text = textMap[key] || "";
                    if (!text) {
                        toast("该字段为空，没有可复制的内容", "err");
                        return;
                    }
                    await tmdbhCopyText(text);
                    toast(`已复制：${tmdbhTruncate(text, 48)}`, "ok");
                } else if (action === "copy-record") {
                    const rec = state.record;
                    if (!rec || !rec.title) {
                        toast("请先搜索并载入来源条目", "err");
                        return;
                    }
                    const text = buildRecordText(rec);
                    await tmdbhCopyText(text);
                    toast(`已复制全部信息（${text.length} 字）`, "ok");
                } else if (action === "source-tab") {
                    state.sourceTab = actionEl.dataset.sourceTab || "douban";
                    renderBody();
                } else if (action === "source-search") {
                    await runSourceSearch(state.sourceTab);
                } else if (action === "candidate-pick") {
                    await pickCandidate(actionEl.dataset.cand);
                } else if (action === "save-api-key") {
                    const input = bodyEl.querySelector('[data-role="api-key"]');
                    const key = input ? String(input.value || "").trim() : "";
                    if (key.length < 8) {
                        toast("API Key 看起来不对（太短）", "err");
                        return;
                    }
                    configStore.update({ tmdb: { apiKey: key } });
                    state.apiKeyEditing = false;
                    toast("API Key 已保存（仅保存在本机脚本存储中）", "ok");
                    renderBody();
                } else if (action === "edit-api-key") {
                    state.apiKeyEditing = true;
                    renderBody();
                } else if (action === "cancel-edit-api-key") {
                    state.apiKeyEditing = false;
                    renderBody();
                } else if (action === "clear-api-key") {
                    configStore.update({ tmdb: { apiKey: "" } });
                    state.apiKeyEditing = false;
                    toast("已清除 API Key", "ok");
                    renderBody();
                } else if (action === "parse-entry") {
                    const textarea = bodyEl.querySelector('[data-role="entry-text"]');
                    state.entryTextDraft = textarea ? textarea.value : "";
                    await runSourceSearch("text");
                } else if (action === "parse-clipboard") {
                    setLoading("parse-clipboard", true);
                    try {
                        const text = await navigator.clipboard.readText();
                        state.entryTextDraft = text;
                        const textarea = bodyEl.querySelector('[data-role="entry-text"]');
                        if (textarea) textarea.value = text;
                        await runSourceSearch("text");
                    } catch (err) {
                        toast(`读取剪贴板失败：${err.message}（可手动粘贴后点解析）`, "err");
                    } finally {
                        setLoading("parse-clipboard", false);
                    }
                } else if (action === "record-clear") {
                    state.record = null;
                    state.candidates = [];
                    state.recordError = "";
                    renderBody();
                    schedulePersistPanelState();
                    toast("已清除来源条目", "ok");
                } else if (action === "poster-load") {
                    await loadPoster();
                } else if (action === "upload-direct") {
                    await directUploadPoster();
                } else if (action === "manual-pick") {
                    pickLocalImage();
                } else if (action === "manual-upload") {
                    await uploadManualImage();
                } else if (action === "upload-refresh") {
                    location.reload();
                } else if (action === "record-poster-upload") {
                    await uploadRecordPoster();
                } else if (action === "baike-episodes") {
                    await fetchRecordBaikeEpisodes();
                } else if (action === "baike-eps-copy") {
                    await copyBaikeEpisodesTsv();
                } else if (action === "baike-eps-fill") {
                    await fillEpisodesFromBaike();
                } else if (action === "groups-load") {
                    await loadGroups();
                } else if (action === "group-details") {
                    await loadGroupDetails(actionEl.dataset.group);
                } else if (action === "subgroup-eps") {
                    await loadSubGroupEpisodes(actionEl.dataset.group, actionEl.dataset.subgroup);
                } else if (action === "group-edit") {
                    const group = state.groups.find((item) => String(item.id) === String(actionEl.dataset.group));
                    if (group) await editGroup(group);
                } else if (action === "group-delete") {
                    const group = state.groups.find((item) => String(item.id) === String(actionEl.dataset.group));
                    if (group) await deleteGroup(group);
                } else if (action === "subgroup-add") {
                    await createSubGroup(actionEl.dataset.group);
                } else if (action === "subgroup-edit") {
                    const details = state.groupDetails[actionEl.dataset.group];
                    const sub = details && Array.isArray(details.groups) ? details.groups.find((item) => String(item.id) === String(actionEl.dataset.subgroup)) : null;
                    if (sub) await editSubGroup(actionEl.dataset.group, sub);
                } else if (action === "subgroup-delete") {
                    const details = state.groupDetails[actionEl.dataset.group];
                    const sub = details && Array.isArray(details.groups) ? details.groups.find((item) => String(item.id) === String(actionEl.dataset.subgroup)) : null;
                    if (sub) await deleteSubGroup(actionEl.dataset.group, sub);
                } else if (action === "subgroup-picker") {
                    openSubGroupPicker(actionEl.dataset.group, actionEl.dataset.subgroup);
                } else if (action === "picker-load") {
                    await loadPickerSeason(actionEl.dataset.group, actionEl.dataset.subgroup);
                } else if (action === "picker-add") {
                    await addPickedEpisodes(actionEl.dataset.group, actionEl.dataset.subgroup);
                } else if (action === "picker-cancel") {
                    state.subGroupPicker = null;
                    renderBody();
                } else if (action === "subgroup-sort") {
                    await sortSubGroupEpisodesAction(actionEl.dataset.group, actionEl.dataset.subgroup);
                } else if (action === "subgroup-ep-remove") {
                    await removeSubGroupEpisodeAction(actionEl.dataset.group, actionEl.dataset.subgroup, actionEl.dataset.media);
                } else if (action === "group-copy-id") {
                    await tmdbhCopyText(pageContext.groupId || "");
                    toast("剧集组 ID 已复制", "ok");
                } else if (action === "group-create") {
                    await createGroupDirect();
                } else if (action === "parse-episodes") {
                    const textarea = bodyEl.querySelector('[data-role="episode-text"]');
                    state.episodeText = textarea ? textarea.value : "";
                    const filtered = parseEpisodeListWithFilters(state.episodeText);
                    state.episodes = filtered.episodes;
                    state.stillStatus = new Map();
                    resetEpisodeProgress();
                    await refreshExistingEpisodes();
                    resetEpisodeSelections();
                    renderBody();
                    toast(state.episodes.length ? `解析出 ${state.episodes.length} 集${filterToastSuffix(filtered)}` : "没有解析出任何分集，请检查格式", state.episodes.length ? "ok" : "err");
                } else if (action === "ep-load-existing") {
                    setLoading("ep-load-existing", true);
                    try {
                        const existing = await listRemoteEpisodes(pageContext.tvId, effectiveSeason());
                        if (!existing.length) {
                            toast("编辑器里没有已有集", "err");
                        } else {
                            state.episodes = existing.map((ep) => ({
                                episodeNumber: ep.episodeNumber,
                                name: ep.name || "",
                                airDate: ep.airDate || "",
                                overview: String(ep.overview || "").replace(/\s*\n\s*/g, " ").trim(),
                                runtime: ep.runtime || 0,
                                stillUrl: ""
                            }));
                            state.stillStatus = new Map();
                            resetEpisodeProgress();
                            state.lastFailed = [];
                            await refreshExistingEpisodes();
                            resetEpisodeSelections();
                            renderBody();
                            toast(`已载入 ${state.episodes.length} 个已有集，勾选后提交即批量修正覆盖（自动走更新接口）`, "ok");
                        }
                    } catch (err) {
                        toast(`载入已有集失败：${err.message}`, "err");
                    } finally {
                        setLoading("ep-load-existing", false);
                    }
                } else if (action === "site-fetch") {
                    const input = bodyEl.querySelector('[data-role="site-url"]');
                    const raw = String(input && input.value || state.siteUrlQuery || "").trim();
                    // 容错：粘贴内容可能带残留前缀（如上次遗留的 "h" 变成 hhttps://…），提取其中的 URL 子串
                    const urlMatch = raw.match(/https?:\/\/\S+/i);
                    setLoading("site-fetch", true);
                    try {
                        if (!raw) {
                            toast("先粘贴平台剧集页链接，或直接输入剧名搜索红果/B站", "err");
                        } else if (urlMatch) {
                            if (urlMatch[0] !== raw) {
                                if (input) input.value = urlMatch[0];
                                state.siteUrlQuery = urlMatch[0];
                            }
                            await fetchEpisodesFromSiteUrl(urlMatch[0]);
                        } else {
                            await searchSiteSources(raw);
                        }
                    } catch (err) {
                        toast(`抓取失败：${err.message}`, "err");
                    } finally {
                        setLoading("site-fetch", false);
                    }
                } else if (action === "site-cand") {
                    const candidate = state.siteCandidates[Number(actionEl.dataset.siteCand)];
                    if (!candidate) return;
                    setLoading("site-fetch", true);
                    try {
                        if (candidate.platform === "bilibili") {
                            const pageUrl = `https://www.bilibili.com/bangumi/play/ss${candidate.id}`;
                            await applySiteEpisodes("哔哩哔哩", await matchSiteSource(pageUrl).fetch(pageUrl, { config: configStore.get() }));
                        } else {
                            await fetchHongguoSeriesById(candidate.id);
                        }
                    } catch (err) {
                        toast(`抓取失败：${err.message}`, "err");
                    } finally {
                        setLoading("site-fetch", false);
                    }
                } else if (action === "site-cover-copy") {
                    const cover = state.siteMeta && state.siteMeta.cover;
                    if (!cover) return;
                    await tmdbhCopyText(cover);
                    toast("封面直链已复制：到「上传图片」页粘贴后可直传为海报", "ok");
                } else if (action === "site-cover-upload") {
                    await uploadSiteCoverAsSeasonPoster();
                } else if (action === "ep-pull-tmdb") {
                    await pullSeasonFromTmdb();
                } else if (action === "ep-generate") {
                    syncEpisodeScheduleFromDom();
                    const result = buildEpisodeSchedule(state.schedule);
                    if (result.error || !result.episodes.length) {
                        toast(result.error || "生成失败：请检查集数与首播日期", "err");
                        return;
                    }
                    state.episodes = result.episodes;
                    state.stillStatus = new Map();
                    resetEpisodeProgress();
                    await refreshExistingEpisodes();
                    resetEpisodeSelections();
                    renderBody();
                    toast(`已生成 ${result.episodes.length} 集（${result.meta.firstDate} ~ ${result.meta.lastDate}，${result.meta.summary}）`, "ok");
                } else if (action === "ep-clipboard") {
                    setLoading("ep-clipboard", true);
                    try {
                        const text = await navigator.clipboard.readText();
                        const textarea = bodyEl.querySelector('[data-role="episode-text"]');
                        if (textarea) textarea.value = text;
                        state.episodeText = text;
                        const filtered = parseEpisodeListWithFilters(text);
                        state.episodes = filtered.episodes;
                        state.stillStatus = new Map();
                        resetEpisodeProgress();
                        await refreshExistingEpisodes();
                        resetEpisodeSelections();
                        renderBody();
                        toast(state.episodes.length ? `已从剪贴板解析出 ${state.episodes.length} 集${filterToastSuffix(filtered)}` : "剪贴板内容没有解析出分集", state.episodes.length ? "ok" : "err");
                    } catch (err) {
                        toast(`读取剪贴板失败：${err.message}`, "err");
                    } finally {
                        setLoading("ep-clipboard", false);
                    }
                } else if (action === "ep-export-tsv") {
                    const tsv = exportEpisodesToTsv(state.episodes);
                    await tmdbhCopyText(tsv);
                    toast(`已复制 ${state.episodes.length} 集为 TSV，可回贴到解析框继续编辑`, "ok");
                } else if (action === "ep-check-all" || action === "ep-check-none") {
                    const checked = action === "ep-check-all";
                    for (const ep of state.episodes) state.epSelected.set(ep.episodeNumber, checked);
                    renderBody();
                } else if (action === "ep-stop") {
                    state.stopRequested = true;
                    toast("将在当前单集提交完成后停止");
                } else if (action === "ep-retry-failed") {
                    const failedSet = new Set(state.lastFailed);
                    for (const ep of state.episodes) state.epSelected.set(ep.episodeNumber, failedSet.has(ep.episodeNumber));
                    for (const number of state.lastFailed) state.epStatus.delete(number);
                    state.finished = false;
                    state.progress = 0;
                    state.progressText = "";
                    renderBody();
                    toast(`已勾选 ${state.lastFailed.length} 个失败集，点击「提交」重试`, "ok");
                } else if (action === "ep-refresh") {
                    location.reload();
                } else if (action === "stills-fetch") {
                    syncEpisodeScheduleFromDom();
                    await fetchAndPrepareStills();
                } else if (action === "stills-submit") {
                    await submitStills();
                } else if (action === "episode-action") {
                    const actionId = actionEl.dataset.epAction;
                    try {
                        const result = await episodeActions.run(actionId, episodeActionEnv());
                        if (typeof result === "string" && result) toast(result, "ok");
                    } catch (err) {
                        toast(`批量操作失败：${err.message}`, "err");
                    }
                } else if (action === "ep-submit") {
                    if (state.submitting) return;
                    syncEpisodeScheduleFromDom();
                    const selected = state.episodes.filter((ep) => state.epSelected.get(ep.episodeNumber) === true);
                    if (!selected.length) {
                        toast("请先勾选要提交的分集", "err");
                        return;
                    }
                    if (selected.length > 200 && !window.confirm(`本次将提交 ${selected.length} 集，数量较大，建议分批操作以免触发风控。确认继续？`)) return;
                    const hasExisting = selected.some((ep) => state.existingNumbers.has(ep.episodeNumber));
                    const hasDup = selected.some((ep) => state.duplicateNumbers.has(ep.episodeNumber));
                    if (hasExisting || hasDup) {
                        const reasons = [hasExisting ? "已存在的集（将走官方更新接口覆盖）" : "", hasDup ? "重复集数" : ""].filter(Boolean).join("和");
                        if (!window.confirm(`勾选中包含${reasons}。确认继续？`)) return;
                    }
                    const season = effectiveSeason();
                    state.submitting = true;
                    state.stopRequested = false;
                    state.finished = false;
                    state.progress = 0;
                    state.progressText = "准备提交…";
                    renderBody();
                    const cfg = configStore.get();
                    const language = state.language || tmdbhDetectEditorLanguage();
                    let done = 0;
                    let failed = 0;
                    let stopped = false;
                    // 提交前现拉一次已有集索引，保证覆盖走的 id/bson_id 是最新值
                    let existingIndex = {};
                    try {
                        const data = await fetchRemoteEpisodesData(pageContext.tvId, season);
                        existingIndex = buildRemoteEpisodeIndex(data);
                        state.existingIndex = existingIndex;
                    } catch (err) { /* 读取失败时仍按新增尝试 */ }
                    await batchAddEpisodes(
                        pageContext.tvId,
                        season,
                        language,
                        selected,
                        { delayMs: cfg.episodes.delayMs, retries: cfg.episodes.retries, existingIndex },
                        {
                            shouldStop: () => state.stopRequested,
                            onStart: (index, total) => {
                                state.progress = index / total;
                                state.progressText = `正在提交第 ${index + 1}/${total} 集…`;
                                updateEpisodeRowUi(selected[index], "提交中…", false);
                            },
                            onResult: (index, total, episode, error) => {
                                done += 1;
                                if (error) {
                                    failed += 1;
                                    state.epStatus.set(episode.episodeNumber, `✗ ${error.message}`);
                                    if (error.auth) toast(error.message, "err");
                                } else {
                                    state.epStatus.set(episode.episodeNumber, "✓");
                                }
                                state.progress = done / total;
                                state.progressText = `已提交 ${done}/${total}${failed ? `，失败 ${failed}` : ""}`;
                                updateEpisodeRowUi(episode, state.epStatus.get(episode.episodeNumber), Boolean(error));
                            }
                        }
                    );
                    stopped = state.stopRequested;
                    const failedNumbers = selected
                        .filter((ep) => String(state.epStatus.get(ep.episodeNumber) || "").startsWith("✗"))
                        .map((ep) => ep.episodeNumber);
                    const succeededNumbers = selected.filter((ep) => !failedNumbers.includes(ep.episodeNumber)).map((ep) => ep.episodeNumber);
                    if (succeededNumbers.length) recordDoneEpisodes(storage, pageContext.tvId, season, succeededNumbers);
                    state.lastFailed = failedNumbers;
                    state.submitting = false;
                    state.stopRequested = false;
                    state.finished = true;
                    state.progress = 1;
                    state.progressText = stopped ? `已停止：成功 ${done - failed} 集，失败 ${failed} 集` : `提交完成：成功 ${done - failed} 集，失败 ${failed} 集`;
                    renderBody();
                    if (failed) {
                        const firstFail = bodyEl.querySelector("td.ep-status.err");
                        if (firstFail && firstFail.closest("tr")) firstFail.closest("tr").scrollIntoView({ behavior: "smooth", block: "center" });
                    }
                    toast(state.progressText, failed ? "err" : "ok");
                }
            } catch (err) {
                toast(`操作失败：${err.message}`, "err");
            }
        });

        // —— 草稿文本即时同步，避免重渲染丢输入 ——
        overlayEl.addEventListener("input", (event) => {
            const target = event.target;
            if (!(target instanceof Element) || !target.dataset) return;
            const role = target.dataset.role;
            if (role === "entry-text") state.entryTextDraft = target.value;
            else if (role === "episode-text") state.episodeText = target.value;
            else if (role === "source-query") state.searchQuery[state.sourceTab] = target.value;
            else if (role === "site-url") state.siteUrlQuery = target.value;
            else if (role === "ep-filter-words") configStore.update({ filters: { words: target.value } });
            else if (role === "poster-url") state.entryPoster = target.value;
            else if (target.dataset.groupName === "name") state.groupDraftName = target.value;
            else if (target.dataset.groupName === "desc") state.groupDraftDesc = target.value;
            else if (target.dataset.gen !== undefined) syncEpisodeScheduleFromDom();
        });

        // —— 表格勾选与行内编辑 ——
        overlayEl.addEventListener("change", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) return;
            if (!(target instanceof HTMLInputElement)) return;
            if (target.dataset.epSeason !== undefined) {
                const season = Math.round(Number(target.value));
                if (Number.isFinite(season) && season >= 0 && season <= 500 && season !== effectiveSeason()) {
                    state.seasonOverride = season;
                    state.episodes = [];
                    state.epStatus = new Map();
                    state.stillStatus = new Map();
                    state.epSelected = new Map();
                    state.finished = false;
                    state.progress = 0;
                    state.progressText = "";
                    refreshExistingEpisodes().then(() => renderBody());
                    toast(`已切换到第 ${season} 季，已有 ${state.existingNumbers.size} 集`, "ok");
                }
                return;
            }
            if (target.dataset.epCheck) {
                state.epSelected.set(Number(target.dataset.epCheck), target.checked);
                const btn = bodyEl.querySelector('[data-action="ep-submit"]');
                if (btn) {
                    const count = state.episodes.filter((ep) => state.epSelected.get(ep.episodeNumber) === true).length;
                    btn.textContent = `▶ 提交勾选的 ${count} 集`;
                    btn.disabled = count === 0;
                }
                return;
            }
            const applyToEpisode = (key, transform) => {
                const number = Number(target.dataset[key]);
                const episode = state.episodes.find((ep) => ep.episodeNumber === number);
                if (episode) transform(episode);
            };
            if (target.dataset.epName) {
                applyToEpisode("epName", (ep) => { ep.name = target.value.trim(); });
            } else if (target.dataset.epRuntime) {
                applyToEpisode("epRuntime", (ep) => {
                    const num = Number(target.value);
                    ep.runtime = Number.isFinite(num) && num > 0 ? Math.round(num) : 0;
                });
            } else if (target.dataset.epOverview) {
                applyToEpisode("epOverview", (ep) => { ep.overview = target.value.trim(); });
            } else if (target.dataset.epStill) {
                applyToEpisode("epStill", (ep) => { ep.stillUrl = target.value.trim(); });
            } else if (target.dataset.epDate) {
                const normalized = normalizeAirDate(target.value);
                applyToEpisode("epDate", (ep) => {
                    if (!normalized && target.value.trim()) {
                        toast("日期格式无法识别（示例 2024-01-01），已还原", "err");
                        target.value = ep.airDate || "";
                    } else {
                        ep.airDate = normalized;
                        target.value = normalized;
                    }
                });
            }
        });

        // —— 剧集组类型下拉与子组选择器勾选 ——
        overlayEl.addEventListener("change", (event) => {
            const target = event.target;
            if (target instanceof HTMLSelectElement && target.dataset.role === "poster-crop") {
                state.posterCropHalf = ["left", "right"].includes(target.value) ? target.value : "";
                return;
            }
            if (target instanceof HTMLSelectElement && target.dataset.groupName === "type") {
                state.groupDraftType = Number(target.value) || 1;
                return;
            }
            if (target instanceof HTMLInputElement && target.dataset.pickEp !== undefined && state.subGroupPicker) {
                if (target.checked) state.subGroupPicker.selected.add(target.dataset.pickEp);
                else state.subGroupPicker.selected.delete(target.dataset.pickEp);
                const addBtn = bodyEl.querySelector('[data-action="picker-add"]');
                if (addBtn) addBtn.textContent = `添加勾选的 ${state.subGroupPicker.selected.size} 集`;
            }
        });

        // 点击遮罩空白处关闭
        overlayEl.addEventListener("mousedown", (event) => {
            if (event.target === overlayEl) setOpen(false);
        });

        // —— 全局快捷键 ——
        document.addEventListener("keydown", (event) => {
            // 输入法组字期的按键（Esc 取消候选词、回车选字，macOS 会带真实 key + isComposing）不算快捷键，
            // 否则在面板输入框打中文一按 Esc 整个浮层就被关掉
            if (event.isComposing || event.keyCode === 229) return;
            if (event.key === "Escape" && overlayEl.classList.contains("open")) {
                setOpen(false);
            }
            if (event.altKey && !event.ctrlKey && !event.metaKey && String(event.key).toLowerCase() === "t") {
                event.preventDefault();
                const hide = host.style.display !== "none";
                host.style.display = hide ? "none" : "";
                tmdbhSession("tmdbh.ballHidden", hide ? "1" : null);
                if (!hide) toast("悬浮球已显示");
            }
        }, true);
        root.addEventListener("keydown", (event) => {
            if (event.isComposing || event.keyCode === 229) return; // 组字期回车是选字，不触发快捷解析
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                const action = state.view === "episodes" ? "parse-episodes" : state.sourceTab === "text" ? "parse-entry" : "source-search";
                const btn = bodyEl.querySelector(`[data-action="${action}"]`);
                if (btn) btn.click();
            }
        });
        const themeMedia = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
        if (themeMedia && typeof themeMedia.addEventListener === "function") {
            themeMedia.addEventListener("change", () => {
                if (configStore.get().theme === "auto") applyTheme();
            });
        }

        applyTheme();
        restoreBallPosition();
        restorePersistedPanelState();
        render();
        // 只点击才显示：刷新/跳转后浮层保持收起，绝不自动弹出（Alt+T 隐藏悬浮球的状态仍保留）
        if (tmdbhSession("tmdbh.ballHidden") === "1") host.style.display = "none";
        window.addEventListener("pagehide", persistPanelStateNow);

        // 剧集组页：预读当前组结构（浮层点开即可见，不发弹窗）
        if (pageContext.groupId && tmdbhTmdbReady(configStore.get())) {
            loadGroupDetails(pageContext.groupId);
        }
        // 季编辑器：预读已有集，给出「下一集建议」（同样只在浮层里可见）
        if (pageContext.kind === "season-edit") {
            refreshExistingEpisodes().then(() => {
                if (state.view === "episodes") renderBody();
            });
        }

        return {
            open: () => setOpen(true),
            close: () => setOpen(false)
        };
    }


    // src/main.js —— 入口：数据源适配器注册、批量操作扩展接口、启动（运行时）
    function tmdbhMakeStorage() {
        const hasGM = typeof GM_getValue === "function" && typeof GM_setValue === "function";
        return {
            get(key) {
                try {
                    return hasGM ? GM_getValue(key) : localStorage.getItem(key);
                } catch (err) {
                    return null;
                }
            },
            set(key, value) {
                try {
                    if (hasGM) GM_setValue(key, value);
                    else localStorage.setItem(key, value);
                } catch (err) {
                    /* 忽略 */
                }
            }
        };
    }

    // —— 对外扩展接口：新数据源 / 批量操作 / 打开面板 ——
    // 用法示例：
    //   TmdbHelper.registerDataSource({ id: "bangumi", name: "Bangumi", async search(q){...}, async findById(id){...} })
    //   TmdbHelper.registerEpisodeAction({ id: "my-op", title: "我的批量操作", when: (env) => true, async run(env) {...} })
    const TMDBH_GLOBAL_KEY = "__tmdbHelper";
    function tmdbhInstallGlobalBridge() {
        const pendingSources = [];
        const pendingActions = [];
        let bridge = null;
        bridge = {
            get version() {
                return (typeof GM_info !== "undefined" && GM_info && GM_info.script && GM_info.script.version) || "?";
            },
            registerDataSource(adapter) {
                if (bridge._sources) bridge._sources.register(adapter);
                else pendingSources.push(adapter);
            },
            registerEpisodeAction(action) {
                if (bridge._actions) bridge._actions.register(action);
                else pendingActions.push(action);
            },
            _sources: null,
            _actions: null,
            _flush(sources, actions, panel) {
                for (const adapter of pendingSources.splice(0)) sources.register(adapter);
                for (const action of pendingActions.splice(0)) actions.register(action);
                bridge._sources = sources;
                bridge._actions = actions;
                bridge._panel = panel;
            },
            _panel: null,
            open() {
                if (bridge._panel) bridge._panel.open();
            },
            close() {
                if (bridge._panel) bridge._panel.close();
            }
        };
        try {
            window[TMDBH_GLOBAL_KEY] = bridge;
        } catch (err) { /* 沙盒环境忽略 */ }
        return bridge;
    }

    (() => {
        const bridge = tmdbhInstallGlobalBridge();
        function boot() {
            const pageContext = detectPageContext(location.pathname);
            if (!tmdbhViewsForContext(pageContext)) return;
            const storage = tmdbhMakeStorage();
            const configStore = new TmdbhConfigStore(storage);
            const sources = createDataSourceRegistry();
            sources.register({
                id: "douban",
                name: "豆瓣",
                async findById(id, env) {
                    return normalizeDoubanDetail(await fetchDoubanDetail(id, env.config));
                },
                async findByImdb(imdbId, env) {
                    return normalizeDoubanDetail(await fetchDoubanDetailByImdb(imdbId, env.config));
                }
            });
            sources.register({
                id: "imdb",
                name: "IMDb",
                async findById(imdbId, env) {
                    if (!/^tt\d+$/i.test(String(imdbId))) throw new Error("无效的 IMDb 编号");
                    // TMDB /find 区分大小写，查询用小写；展示值保留大写
                    const found = await tmdbhTmdbFindImdb(env.config, String(imdbId).toLowerCase());
                    const first = [...found.tv, ...found.movies][0];
                    if (!first) throw new Error("TMDB 没有该 IMDb 编号对应的条目");
                    const mediaType = first.name && !first.title ? "tv" : "movie";
                    const detail = await tmdbhTmdbGet(env.config, `/${mediaType}/${first.id}`, { append_to_response: "credits" });
                    const record = normalizeTmdbDetail(detail, mediaType);
                    record.imdb = String(imdbId).toUpperCase();
                    return record;
                }
            });
            sources.register({
                id: "baike",
                name: "百度百科",
                hint: "支持 关键词 / 百科条目链接（baike.baidu.com/item/…）",
                alwaysLoadDetail: true, // 搜索候选只是浅摘要，点卡片必须拉词条详情
                async search(query, env) {
                    return searchBaikeEntries(query, env.config);
                },
                async findById(id, env) {
                    // 详情定位优先用候选的词条 URL（多义词/重定向依赖完整路径），其次用搜索词本身
                    const candidate = env && env.candidate;
                    const target = (candidate && candidate.url) || String(id || "").trim();
                    return normalizeBaikeDetail(await fetchBaikeDetail(target, env.config));
                }
            });
            sources.register({
                id: "text",
                name: "粘贴文本",
                findById: null
            });
            const episodeActions = createEpisodeActionRegistry();
            episodeActions.register({
                id: "builtin-retry-failed",
                title: "↻ 重试失败集",
                when: () => false, // 失败集重试走顶部按钮；此条目用于演示批量操作注册表
                run: async () => ""
            });
            episodeActions.register({
                id: "builtin-stills-pipeline",
                title: "🎬 缩略图一键抓取+上传（勾选集）",
                when: (env) => env && Array.isArray(env.episodes) && env.episodes.some((ep) => ep.stillUrl),
                run: async (env) => {
                    const targets = env.episodes.filter((ep) => env.selectedNumbers.includes(ep.episodeNumber) && ep.stillUrl);
                    if (!targets.length) return "勾选的集里没有填缩略图 URL";
                    await env.fetchAndPrepareStills();
                    await env.submitStills();
                    return "缩略图管线执行完毕，请看各集状态";
                }
            });
            const panel = createTmdbhPanel({ configStore, storage, pageContext, sources, episodeActions });
            bridge._flush(sources, episodeActions, panel);
        }
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", boot, { once: true });
        } else {
            boot();
        }
    })();


})();
