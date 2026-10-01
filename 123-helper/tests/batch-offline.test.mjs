// 批量离线下载模块回归（1.4.5 新增，按仓库口径重写：走 Pan123Api、不硬编码镜像域名）。
//
// 覆盖三块：① 链接解析/去重/设置夹取等纯逻辑；② 解析失败的分类（123 对无效链接也回 err_code=3，
// 旧写法一律当「服务繁忙(限流)」会让用户白等，只有文案像限流才当限流）；③ 源码契约——凭据只发同源、
// 入口按文案定位、未登录/分享页不注入。
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const source = fs.readFileSync(fileURLToPath(new URL("../123-helper.user.js", import.meta.url)), "utf8");
const lines = source.split("\n");
const from = lines.findIndex((line) => line.includes("var RESOLVE_PATH ="));
const to = lines.findIndex((line) => line.includes("var state = { tasks: []"));
assert.ok(from > 0 && to > from, "无法在脚本中定位批量离线模块的逻辑区段");
const code = lines.slice(from, to).join("\n");

const calls = [];
const sandbox = {
  console, Set, Map, RegExp, JSON, Math, Number, String, Array, Object, Boolean, Error, Promise, Symbol, Intl, setTimeout,
  location: { origin: "https://yun.123pan.cn", hostname: "yun.123pan.cn" },
  GM_getValue: (key, fallback) => sandbox.__storage[key] ?? fallback,
  GM_setValue: (key, value) => {
    sandbox.__storage[key] = value;
  },
  __storage: {}
};
sandbox.globalThis = sandbox;
sandbox.Pan123Api = class {
  constructor(options) {
    this.options = options;
  }
  async request(method, path, options = {}) {
    calls.push({ method, path, body: options.body, host: this.options.host });
    return sandbox.__response ? sandbox.__response() : { code: 0, data: {} };
  }
};
sandbox.isOfficialPanPortalHost = () => sandbox.__hostOk !== false;
sandbox.readCredentialStorage = (name) => (name === "authorToken" ? (sandbox.__token ?? "jwt-token") : "");
sandbox.isAuthFailure = (status, code, message) => Number(code) === 401 || /登录已过期|未登录/.test(String(message || ""));
vm.createContext(sandbox);
vm.runInContext(`${code}
globalThis.__bo = { parseLinks, clampNumber, loadSettings, saveSettings, resolveFailure, resolveLink, submitTask, batchOfflineEligible, offlineApi, RESOLVE_PATH, SUBMIT_PATH, SETTINGS_KEY };`, sandbox, { filename: "123-helper.user.js" });
const { parseLinks, clampNumber, loadSettings, saveSettings, resolveFailure, resolveLink, submitTask, batchOfflineEligible, RESOLVE_PATH, SUBMIT_PATH, SETTINGS_KEY } = sandbox.__bo;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test("链接解析：按行拆分、去重、只留直链/磁力/ed2k", () => {
  const links = parseLinks(" https://a.com/x.mkv \nhttps://a.com/x.mkv\nmagnet:?xt=urn:btih:abc\n\ned2k://|file|y.mkv|1|\njavascript:alert(1)\n乱写的");
  assert.equal(links.join(" | "), "https://a.com/x.mkv | magnet:?xt=urn:btih:abc | ed2k://|file|y.mkv|1|");
});

test("设置夹取：越界回落区间、非数字用默认", () => {
  assert.equal(clampNumber("900", 1, 120, 1), 120);
  assert.equal(clampNumber("-5", 1, 120, 3), 1);
  assert.equal(clampNumber("abc", 1, 10, 7), 7);
  sandbox.__storage[SETTINGS_KEY] = { interval: 900, retry: -3, retryWait: "x" };
  assert.deepEqual({ ...loadSettings() }, { interval: 120, retry: 0, retryWait: 10 });
});

test("设置读写走脚本存储（跨域名共享，不落 localStorage）", () => {
  saveSettings({ interval: 2, retry: 1, retryWait: 5 });
  assert.deepEqual(sandbox.__storage[SETTINGS_KEY], { interval: 2, retry: 1, retryWait: 5 });
});

test("解析失败分类：文案像限流才算限流，其它按服务端原因报", () => {
  assert.equal(resolveFailure({ err_code: 3, err_msg: "请求过于频繁，请稍后再试" }).limited, true);
  assert.equal(resolveFailure({ err_code: 3, err_msg: "" }).limited, undefined, "空文案的 err_code 3 不是限流（实测无效链接也回 3）");
  assert.match(resolveFailure({ err_code: 3, err_msg: "" }).message, /错误码 3/);
  assert.match(resolveFailure({ err_code: 1, err_msg: "" }).message, /链接无法访问或不支持/);
  assert.equal(resolveFailure({ err_code: 5, err_msg: "" }).exists, true, "已存在按成功跳过处理");
});

test("resolve 成功：取 resource_id 与文件 id 列表", async () => {
  sandbox.__response = () => ({ code: 0, data: { list: [{ result: 0, id: "r1", name: "  某剧 S01E01  ", size: 10, files: [{ id: 7 }, { id: 0 }, { id: 8 }] }] } });
  const resolved = await resolveLink("https://a.com/x.mkv", null);
  assert.equal(resolved.resourceId, "r1");
  assert.equal(resolved.fileIds.join(","), "7,8", "0 不是合法文件 id");
  assert.equal(resolved.name, "某剧 S01E01");
});

test("resolve 失败：抛出可分类的错误", async () => {
  sandbox.__response = () => ({ code: 0, data: { list: [{ result: 1, err_code: 3, err_msg: "" }] } });
  await assert.rejects(() => resolveLink("https://a.com/bad", null), /错误码 3/);
  sandbox.__response = () => ({ code: 401, message: "未登录" });
  await assert.rejects(() => resolveLink("https://a.com/bad", null), (error) => error.expired === true, "登录过期要单独标记");
});

test("submit 成功与失败文案", async () => {
  sandbox.__response = () => ({ code: 0, data: { task_list: [{ result: 0, task_id: "t1" }] } });
  assert.equal(await submitTask({ resourceId: "r1", fileIds: [7] }, null), "t1");
  sandbox.__response = () => ({ code: 0, data: { task_list: [{ result: 1, err_code: 9, err_msg: "空间不足" }] } });
  await assert.rejects(() => submitTask({ resourceId: "r1", fileIds: [7] }, null), /空间不足/);
});

test("接口一律走 Pan123Api 的同源 host（不再硬编码镜像域名发凭据）", async () => {
  calls.length = 0;
  sandbox.__response = () => ({ code: 0, data: { list: [{ result: 0, id: "r1", files: [{ id: 7 }] }] } });
  await resolveLink("https://a.com/x.mkv", null);
  sandbox.__response = () => ({ code: 0, data: { task_list: [{ result: 0, task_id: "t1" }] } });
  await submitTask({ resourceId: "r1", fileIds: [7] }, null);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].path, RESOLVE_PATH);
  assert.equal(calls[1].path, SUBMIT_PATH);
  for (const call of calls) assert.equal(call.host, "https://yun.123pan.cn", "应交给 Pan123Api 走页面同源（线路与回退由请求层管）");
});

test("入口生效条件：未登录或非网盘域名时不注入", () => {
  assert.equal(batchOfflineEligible(), true);
  sandbox.__token = "";
  assert.equal(batchOfflineEligible(), false, "未登录不该有入口");
  sandbox.__token = "jwt-token";
  sandbox.__hostOk = false;
  assert.equal(batchOfflineEligible(), false, "分享页/登录页不该有入口");
  sandbox.__hostOk = true;
});

test("源码契约：不写死第三方域名、不按 CSS Module 哈希类名定位菜单", () => {
  const moduleStart = lines.findIndex((line) => line.includes("// src/batch-offline.js"));
  assert.ok(moduleStart > 0, "缺少 src/batch-offline.js 区段标记");
  const module = lines.slice(moduleStart).join("\n");
  assert.ok(!/api\.123278\.com|123865|https?:\/\/(?!example\.com)[a-z0-9.-]+\.(com|cn)/i.test(module.replace(/\/\/.*$/gm, "")), "模块里不该出现写死的下载域名");
  assert.ok(!module.includes("mfy_h-menu-module"), "不该依赖 CSS Module 哈希类名定位原生菜单");
  assert.ok(module.includes("var ANCHOR_LABEL ="), "应按「查看离线下载任务」文案定位");
  assert.ok(module.includes("if (!batchOfflineEligible()) return;"), "扫描前先做路由与登录门控");
  assert.ok(!/GM_registerMenuCommand/.test(module), "入口只挂原生菜单，不再注册篡改猴菜单");
});

// —— 原生菜单注入：最小假 DOM（重复项与「新建链接下载任务」收起来的回归） ——
class Node2 {
  constructor(tag, text = "", children = []) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.attrs = {};
    this.text = text;
    this.parent = null;
    this.children = [];
    for (const child of children) this.append(child);
  }
  append(child) { child.parent = this; this.children.push(child); return this; }
  get parentElement() { return this.parent; }   // 脚本按 DOM 语义读 parentElement
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  removeAttribute(name) { delete this.attrs[name]; }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.children = []; this.text = String(value); }   // 真 DOM 的 textContent 赋值会清空子节点
  get firstElementChild() { return this.children[0] || null; }
  descendants() { const out = []; const walk = (node) => { for (const child of node.children) { out.push(child); walk(child); } }; walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  addEventListener() { this.listeners = (this.listeners || []) + 1; }
  matchesPart(part) {
    const scope = /^:scope\s*>\s*(.+)$/.exec(part);
    if (scope) return this.parent ? this.parent.children.includes(this) && this.matchesPart(scope[1].trim()) : false;
    const attr = /^\[([a-zA-Z][\w-]*)(?:=([^\]]*))?\]$/.exec(part);
    if (attr) {
      const value = this.getAttribute(attr[1]);
      if (value === null) return false;
      return attr[2] === undefined || value === attr[2].replace(/^["']|["']$/g, "");
    }
    if (part.startsWith(".")) return String(this.getAttribute("class") || "").split(/\s+/).includes(part.slice(1));
    return this.tagName === part.toUpperCase();
  }
  querySelectorAll(sel) { const parts = sel.split(",").map((part) => part.trim()).filter(Boolean); return this.descendants().filter((node) => parts.some((part) => node.matchesPart(part))); }
  closest(sel) { const parts = sel.split(",").map((part) => part.trim()).filter(Boolean); let node = this; while (node) { if (parts.some((part) => node.matchesPart(part.split(/\s/)[0]))) return node; node = node.parent; } return null; }
  after(node) { const index = this.parent.children.indexOf(this); this.parent.children.splice(index + 1, 0, node); node.parent = this.parent; }
  cloneNode() { const copy = new Node2(this.tagName, this.text); copy.attrs = { ...this.attrs }; for (const child of this.children) copy.append(child.cloneNode()); return copy; }
}
const menuSliceFrom = lines.findIndex((line) => line.includes("function closeNativeDropdown()"));
const menuSliceTo = lines.findIndex((line) => line.includes("var scanQueued = false;"));
assert.ok(menuSliceFrom > 0 && menuSliceTo > menuSliceFrom, "无法在脚本中定位批量离线的菜单注入区段");
const menuCode = lines.slice(menuSliceFrom, menuSliceTo).join("\n");
const menuSandbox = { console, Set, Map, String, Array, Object, Boolean, RegExp, JSON, Number, Math };
menuSandbox.batchOfflineEligible = () => menuSandbox.__ok !== false;
menuSandbox.document = { querySelectorAll: (sel) => (menuSandbox.__root ? menuSandbox.__root.querySelectorAll(sel) : []) };
// 三个常量声明在菜单区段之前，从源码里按名字取值后注入沙箱（避免测试里手写常量值）
const constOf = (name) => {
  const line = lines.find((entry) => entry.includes(`var ${name} =`));
  assert.ok(line, `源码里找不到 ${name}`);
  return JSON.parse(`"${/=\s*"([^"]*)"/.exec(line)[1]}"`);
};
menuSandbox.MENU_FLAG = constOf("MENU_FLAG");
menuSandbox.ENTRY_LABEL = constOf("ENTRY_LABEL");
menuSandbox.ANCHOR_LABEL = constOf("ANCHOR_LABEL");
menuSandbox.globalThis = menuSandbox;
vm.createContext(menuSandbox);
vm.runInContext(`${menuCode}
globalThis.__menu = { scanMenus };`, menuSandbox, { filename: "123-helper.user.js" });
const { scanMenus } = menuSandbox.__menu;
const { MENU_FLAG, ENTRY_LABEL, ANCHOR_LABEL } = menuSandbox;
// 官方结构：li 里只套一层 span 放文字（文字不重复出现在 li 自身）
const item = (label) => new Node2("li", "", [new Node2("span", label)]);
const nativeMenu = () => new Node2("ul", "", [item("新建链接下载任务"), item("新建BT任务"), item(ANCHOR_LABEL)]);

test("菜单只注入一项：内层 span 与 li 文本相同也不会重复", () => {
  const menu = nativeMenu();
  menuSandbox.__root = new Node2("div", "", [menu]);
  scanMenus();
  const injected = menu.children.filter((child) => child.getAttribute(MENU_FLAG) !== null);
  assert.equal(injected.length, 1, `实际注入 ${injected.length} 项`);
  assert.equal(injected[0].textContent, ENTRY_LABEL);
  assert.ok(menu.children.indexOf(injected[0]) === menu.children.findIndex((child) => child.textContent === ANCHOR_LABEL) + 1, "应紧跟在「查看离线下载任务」后面");
});

test("重复扫描不叠加，且只藏「新建链接下载任务」", () => {
  const menu = nativeMenu();
  menuSandbox.__root = new Node2("div", "", [menu]);
  scanMenus();
  scanMenus();
  assert.equal(menu.children.filter((child) => child.getAttribute(MENU_FLAG) !== null).length, 1, "两次扫描仍只有一项");
  const trimmed = menu.children.filter((child) => child.getAttribute("data-c123-menu-trimmed") === "true").map((child) => child.textContent);
  assert.deepEqual(trimmed, ["新建链接下载任务"], `被藏起来的项：${trimmed}`);
  const kept = menu.children.filter((child) => child.getAttribute("data-c123-menu-trimmed") !== "true" && child.getAttribute(MENU_FLAG) === null).map((child) => child.textContent);
  assert.deepEqual(kept, ["新建BT任务", ANCHOR_LABEL], "其余官方项不动");
});

test("未登录或非网盘页面不注入菜单", () => {
  const menu = nativeMenu();
  menuSandbox.__root = new Node2("div", "", [menu]);
  menuSandbox.__ok = false;
  scanMenus();
  assert.equal(menu.children.filter((child) => child.getAttribute(MENU_FLAG) !== null).length, 0);
  menuSandbox.__ok = true;
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`  ok ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`  FAIL ${name}: ${error.message}`);
  }
}
if (failed) {
  console.error(`\n${failed}/${cases.length} 个用例失败`);
  process.exit(1);
}
console.log(`\nbatch-offline: 全部 ${cases.length} 个用例通过`);
