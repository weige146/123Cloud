// 分享页搜索（1.3.14）回归：官方 /api/search/link 接口的请求参数与响应归一化、
// 游标（next，含 &#*& 分隔符）原样透传、网关 HTML 兜底、限流文案透传、
// 结果条目路径/键/排序纯函数、官方 HighLight → <mark> 的安全转换、分类 chips 映射。
// 用法：node 油猴脚本/123-helper/tests/share-page-search.test.mjs
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
  slice("// src/api.js", "// src/core/categories.js"),
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js"),
  slice("// src/public-share-search.js", "// src/core/release-group.js")
].join("\n");
const driver = `;
globalThis.__mod = {
  Pan123Api,
  normalizedText,
  fileExtensionOf,
  pathOfEntry,
  searchEntryKey,
  sortSearchMatches,
  appendHighlightMarkup,
  appendHighlightedText,
  SEARCH_TYPE_FILTERS,
  ensurePublicShareSearch,
  resolveCheckedEntries,
  buildSearchCsvText,
  collectSearchFastlinkFiles
};
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://1855034111.share.123pan.cn", hostname: "1855034111.share.123pan.cn" },
  setTimeout, clearTimeout, AbortController,
  // 模块里只引用不定义的 UI 工具（本测试不触发 DOM 路径）
  icon: () => "",
  copyText: async () => {},
  showStatus: () => {},
  downloadText: () => {},
  downloadJson: () => {},
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const mod = sandbox.__mod;
const api = new mod.Pan123Api({ host: "https://1855034111.share.123pan.cn" });

// —— 用例 1：请求参数与响应归一化（文件夹 + 带特征值文件混合）——
{
  const calls = [];
  sandbox.fetch = async (url, opts) => {
    calls.push({ url: String(url), opts });
    return {
      ok: true,
      status: 200,
      json: async () => ({
        code: 0,
        data: {
          Total: 2,
          Next: "-1",
          InfoList: [
            { FileId: "34121969", FileName: "咒术回战 (2020)", Type: 1, Size: 366226946618, ParentFileId: 23143798, ParentName: "18-", HighLight: "<123pan_strong>咒术</123pan_strong>回战 (2020)", UpdateAt: "2026-09-11T16:24:44+08:00" },
            { FileId: 31144424, FileName: "租借女友.S05E04.mkv", Type: 0, Size: 1024, Etag: "D0833E1D896AF66019F009B0CF836CF8", S3KeyFlag: "1849835394-0", ParentFileId: 31388140, ParentName: "Season 5" }
          ]
        },
        message: "ok"
      })
    };
  };
  const result = await api.searchPublicShare("咒术", {
    shareKey: "v9WWvd-tvHQd",
    sharePwd: "EWSp",
    limit: 30,
    category: 2,
    parentFileId: "23143798",
    next: "350&#*&0&#*&15&#*&31144419"
  });
  assert.equal(calls.length, 1);
  const url = new URL(calls[0].url);
  assert.equal(url.origin + url.pathname, "https://1855034111.share.123pan.cn/api/search/link");
  assert.equal(url.searchParams.get("share_key"), "v9WWvd-tvHQd");
  assert.equal(url.searchParams.get("query"), "咒术");
  assert.equal(url.searchParams.get("limit"), "30");
  assert.equal(url.searchParams.get("share_pwd"), "EWSp");
  assert.equal(url.searchParams.get("parent_file_id"), "23143798");
  assert.equal(url.searchParams.get("file_category"), "2");
  // 官方游标含 &#*& 分隔符，必须原样透传（URLSearchParams 负责编码）
  assert.equal(url.searchParams.get("next"), "350&#*&0&#*&15&#*&31144419");
  // 免登录接口：不带鉴权头
  assert.equal(calls[0].opts.headers.authorization, undefined);
  assert.equal(result.total, 2);
  assert.equal(result.next, "-1");
  assert.equal(result.files.length, 2);
  assert.deepEqual([result.files[0].type, result.files[1].type].join(","), "1,0");
  assert.equal(result.files[0].highlight, "<123pan_strong>咒术</123pan_strong>回战 (2020)");
  assert.equal(result.files[0].parentName, "18-");
  assert.equal(result.files[1].etag, "D0833E1D896AF66019F009B0CF836CF8");
  assert.equal(result.files[1].s3KeyFlag, "1849835394-0");
  assert.equal(result.files[1].updateAt, "");
  console.log("ok 用例1 请求参数与响应归一化");
}

// —— 用例 2：缺关键词 / 缺分享 Key / 网关 HTML 兜底 ——
{
  await assert.rejects(() => api.searchPublicShare("  ", { shareKey: "k" }), /关键词/);
  await assert.rejects(() => api.searchPublicShare("咒术", {}), /分享 Key/);
  sandbox.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  await assert.rejects(() => api.searchPublicShare("咒术", { shareKey: "k", pacing: null }), /异常内容/);
  console.log("ok 用例2 参数校验与 HTML 兜底");
}

// —— 用例 3：限流文案原样透传（pacing:null 走通用重试，仍终局失败）——
{
  sandbox.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ code: "429", message: "分享接口请求过于频繁" })
  });
  await assert.rejects(() => api.searchPublicShare("咒术", { shareKey: "k", pacing: null, attempts: 2 }), /频繁/);
  console.log("ok 用例3 限流文案透传");
}

// —— 用例 4：空结果 ——
{
  sandbox.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ code: 0, data: { Total: 0, Next: "-1", InfoList: [] }, message: "ok" })
  });
  const result = await api.searchPublicShare("不存在的词", { shareKey: "k", pacing: null });
  assert.deepEqual([result.files.length, result.total, result.next].join(","), "0,0,-1");
  console.log("ok 用例4 空结果");
}

// —— 用例 5：纯函数（路径 / 键 / 扩展名 / 排序 / chips 映射）——
{
  assert.equal(mod.pathOfEntry({ parentName: "Season 5", name: "a.mkv" }), "Season 5/a.mkv");
  assert.equal(mod.pathOfEntry({ name: "根目录文件.mkv" }), "根目录文件.mkv");
  assert.equal(mod.searchEntryKey({ id: "7", parentName: "S", name: "a.mkv" }), "7|S/a.mkv");
  assert.equal(mod.fileExtensionOf("A.B.MKV"), "mkv");
  assert.equal(mod.fileExtensionOf("无扩展名"), "");
  const items = [
    { name: "b", parentName: "x", size: 10, id: "1" },
    { name: "a", parentName: "y", size: 30, id: "2" },
    { name: "c", parentName: "x", size: 20, id: "3" }
  ];
  assert.equal(mod.sortSearchMatches(items, "name").map((i) => i.id).join(","), "2,1,3");
  assert.equal(mod.sortSearchMatches(items, "size").map((i) => i.id).join(","), "2,3,1");
  // 相关度 = 官方接口顺序，不动
  assert.equal(mod.sortSearchMatches(items, "relevance").map((i) => i.id).join(","), "1,2,3");
  assert.equal(mod.normalizedText("  A  B "), "a b");
  const byKey = Object.fromEntries(mod.SEARCH_TYPE_FILTERS.map((chip) => [chip.key, chip.category ?? null]));
  assert.equal(mod.SEARCH_TYPE_FILTERS.length, 8);
  assert.deepEqual(["all", "image", "video", "doc", "audio", "folder", "zip", "other"].map((k) => String(byKey[k])).join(","), "null,1,2,3,4,5,6,7");
  console.log("ok 用例5 纯函数与 chips 映射");
}

// —— 用例 6：官方 HighLight → <mark> 的安全转换（不 innerHTML，注入内容只会变文本）——
{
  const makeNode = (kind) => ({
    kind,
    children: [],
    textContent: "",
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; }
  });
  const fakeTarget = {
    createElement: () => makeNode("element"),
    createTextNode: (text) => {
      const node = makeNode("#text");
      node.textContent = text;
      return node;
    },
    createDocumentFragment: () => makeNode("#fragment")
  };
  const flatten = (node) => node.children.flatMap((child) => {
    if (child.kind === "#text") return [child.textContent];
    if (child.kind === "#fragment") return flatten(child);
    return [`<${child.textContent}>`];
  });
  const element = makeNode("element");
  mod.appendHighlightMarkup(fakeTarget, element, { name: "咒术回战", highlight: "<123pan_strong>咒术</123pan_strong>回战" }, []);
  assert.equal(flatten(element).join(""), "<咒术>回战");
  // 没有官方高亮：退回本地关键词高亮
  const element2 = makeNode("element");
  mod.appendHighlightMarkup(fakeTarget, element2, { name: "Jujutsu Kaisen S01.mkv", highlight: "" }, ["kaisen"]);
  assert.ok(flatten(element2).join("").includes("<Kaisen>"));
  // 官方高亮缺失时，名称里的 HTML 记号必须保持纯文本（防注入）
  const element3 = makeNode("element");
  mod.appendHighlightMarkup(fakeTarget, element3, { name: "<img src=x onerror=1>.mkv", highlight: "" }, []);
  assert.equal(element3.textContent, "<img src=x onerror=1>.mkv");
  assert.equal(element3.children.length, 0);
  console.log("ok 用例6 高亮转换与防注入");
}

// —— 用例 7：ensurePublicShareSearch 用假 DOM 真跑一遍构建（回归 1.3.14 首版的
//    TDZ 崩溃：addEventListener("click", exportCsv) 在 const 初始化前求值，
//    导致搜索框永远挂不上且异常被 MutationObserver 吞掉）——
{
  const makeElement = (tag) => {
    const node = {
      tagName: String(tag || "div").toUpperCase(),
      kind: "element",
      children: [],
      listeners: {},
      dataset: {},
      attributes: {},
      style: {},
      className: "",
      id: "",
      hidden: false,
      title: "",
      textContent: "",
      innerHTML: "",
      checked: false,
      type: "",
      value: "",
      append(...kids) { node.children.push(...kids); },
      setAttribute(key, value) { node.attributes[key] = String(value); },
      getAttribute(key) { return node.attributes[key] ?? null; },
      addEventListener(type, fn) { (node.listeners[type] = node.listeners[type] || []).push(fn); },
      removeEventListener() {},
      replaceChildren(...kids) { node.children = kids; },
      remove() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      contains() { return false; },
      scrollIntoView() {},
      closest(selector) { return String(selector) === ".share-search-input" ? node : null; },
      parentElement: null
    };
    return node;
  };
  const anchor = makeElement("div");
  anchor.className = "share-search-input";
  const officialInput = makeElement("input");
  officialInput.placeholder = "搜索全部文件";
  officialInput.closest = (selector) => String(selector) === ".share-search-input" ? anchor : null;
  const fakeDoc = {
    body: makeElement("body"),
    head: makeElement("head"),
    documentElement: makeElement("html"),
    defaultView: null,
    createElement: (tag) => makeElement(tag),
    createTextNode: (text) => {
      const node = makeElement("#text");
      node.textContent = text;
      return node;
    },
    createDocumentFragment: () => makeElement("#fragment"),
    getElementById: () => null,
    querySelector: (selector) => String(selector) === "input.share-search-input" ? officialInput : null,
    querySelectorAll: () => [],
    addEventListener() {}
  };
  const root2 = mod.ensurePublicShareSearch(fakeDoc, {
    api: { searchPublicShare: () => Promise.resolve({ files: [], total: 0, next: "-1" }) },
    locationRef: { href: "https://1855034111.share.123pan.cn/123pan/v9WWvd-tvHQd?pwd=EWSp" }
  });
  assert.ok(root2, "ensurePublicShareSearch 应返回面板根节点");
  assert.ok(anchor.children.includes(root2), "面板应挂到官方搜索框容器正下方");
  assert.equal(anchor.style.position, "relative");
  assert.equal(root2.className, "c123-public-search");
  assert.equal(officialInput.dataset.c123SearchWired, "true");
  assert.deepEqual([root2.children.length].join(","), "1");
  assert.equal(fakeDoc.__CLOUD123_PUBLIC_SHARE_SEARCH_STATE__.share.shareKey, "v9WWvd-tvHQd");
  assert.equal(fakeDoc.__CLOUD123_PUBLIC_SHARE_SEARCH_STATE__.share.sharePwd, "EWSp");
  const [results] = root2.children;
  assert.equal(results.className, "c123-public-search-results");
  assert.equal(results.hidden, true);
  const [panelbar, summary, filters, list] = results.children;
  assert.equal(panelbar.className, "c123-public-search-panelbar");
  assert.equal(summary.className, "c123-public-search-summary");
  assert.equal(filters.className, "c123-public-search-filters");
  // 8 个分类 chip + 排序按钮；批量操作容器（生成秒传 / 导出 CSV / 清空已选）在面板头
  assert.equal(filters.children.length, 9);
  assert.equal(panelbar.children.length, 2);
  const [, actions] = panelbar.children;
  assert.equal(actions.className, "c123-public-search-actions");
  assert.equal(actions.children.length, 3);
  assert.equal(list.className, "c123-public-search-list");
  // CSS 已注入 head，且外层点击关闭的监听挂到 document 上
  assert.equal(fakeDoc.head.children[0].id, "c123-public-search-css");
  console.log("ok 用例7 面板构建（复用官方搜索框，假 DOM 全流程）");
}

// —— 用例 8：官方结果页勾选行匹配 + CSV 文本（BOM/表头/引号转义/文件夹无大小）——
{
  const entries = [
    { id: "1", name: "咒术回战 (2020)", type: 1, parentName: "18-", size: 0, etag: "", updateAt: "2026-09-11" },
    { id: "2", name: "租借女友.S05E04.mkv", type: 0, parentName: "Season 5", size: 1024, etag: "d0833e1d896af66019f009b0cf836cf8", updateAt: "2026-09-12" },
    { id: "3", name: "同名.mkv", type: 0, parentName: "A", size: 1, etag: "ab".repeat(16), updateAt: "" },
    { id: "4", name: "同名.mkv", type: 0, parentName: "B", size: 2, etag: "cd".repeat(16), updateAt: "" }
  ];
  const checked = [
    { name: "咒术回战 (2020)", parentName: "18-" },
    { name: "租借女友.S05E04.mkv", parentName: "Season 5" },
    { name: "同名.mkv", parentName: "B" },
    { name: "不存在的文件.mkv", parentName: "X" }
  ];
  const { matched, missing } = mod.resolveCheckedEntries(checked, entries);
  assert.deepEqual(matched.map((e) => e.id).join(","), "1,2,4");
  assert.deepEqual(missing.length, 1);
  // 同名文件用 parentName 区分：B 目录命中 id=4 而不是第一个同名
  const csv = mod.buildSearchCsvText(matched);
  assert.ok(csv.startsWith("\uFEFF"));
  const lines = csv.slice(1).split("\r\n");
  assert.equal(lines[0], "路径,类型,大小,修改时间,Etag");
  assert.ok(lines.includes("\"18-/咒术回战 (2020)\",\"文件夹\",\"\",\"2026-09-11\",\"\""));
  assert.ok(lines.some((l) => l.includes("\"Season 5/租借女友.S05E04.mkv\"") && l.includes("\"d0833e1d896af66019f009b0cf836cf8\"")));
  console.log("ok 用例8 官方勾选匹配与 CSV 文本");
}

// —— 用例 9：collectSearchFastlinkFiles 端到端（回归 1.3.14 返回键名 {files} 与
//    调用方解构 {filtered} 不一致的 TypeError）——
{
  sandbox.fetch = async (url) => {
    if (String(url).includes("/b/api/share/get")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ code: 0, data: { Next: "-1", InfoList: [
          { FileId: 900, FileName: "S01E01.mkv", Type: 0, Size: 123, Etag: "aa".repeat(16), S3KeyFlag: "9-0", ParentFileId: 34121969 },
          { FileId: 901, FileName: "S01E02.mkv", Type: 0, Size: 456, Etag: "bb".repeat(16), S3KeyFlag: "9-0", ParentFileId: 34121969 }
        ] } })
      };
    }
    throw new Error("unexpected fetch: " + String(url));
  };
  const api2 = new mod.Pan123Api({ host: "https://x.share.123pan.cn" });
  const result9 = await mod.collectSearchFastlinkFiles(api2, { shareKey: "v9WWvd", sharePwd: "EW" }, [
    { id: "34121969", name: "咒术回战 (2020)", type: 1, parentName: "18-", size: 0, etag: "" },
    { id: "2", name: "a.mkv", type: 0, parentName: "P", size: 9, etag: "cc".repeat(16) }
  ], () => {});
  assert.ok(Array.isArray(result9.filtered), "必须返回 filtered 数组（调用方按 { filtered, skipped } 解构）");
  assert.equal(result9.filtered.length, 3);
  assert.equal(result9.skipped, 0);
  const json = JSON.stringify(result9.filtered.map((f) => f.path));
  assert.ok(json.includes("咒术回战 (2020)/S01E01.mkv"));
  assert.ok(json.includes("P/a.mkv"));
  console.log("ok 用例9 秒传清单端到端（文件夹子树扫描 + 文件直出）");
}

console.log("share-page-search 全部通过");
