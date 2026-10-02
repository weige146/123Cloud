// 工具栏挂载点与「官方选中条」排版回归（1.4.5 防闪烁改造）。
//
// 实测背景（2026-10-01，逐帧采样）：官方勾选/取消时把 .home-operator-button-group 的整棵子树
// 换成 .file-operator-group，旧版把助手按钮插进 .file-operator-group 里 → 每次都被 React 连锅端走
// （取消方向助手整条缺席 ~95ms、勾选方向助手按钮迟到 ~150ms），这就是闪烁。修法：助手按钮只挂
// 稳定父节点，位置与显隐交给 CSS（order + :has(.file-operator-group)），折叠与「官方自带重命名」
// 在 MutationObserver 同步阶段打标记。这里钉住这套新契约，防止被改回插进官方条里。
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const source = fs.readFileSync(fileURLToPath(new URL("../123-helper.user.js", import.meta.url)), "utf8");
const lines = source.split("\n");
const from = lines.findIndex((line) => line.includes("var SELECTORS = {"));
const to = lines.findIndex((line) => line.includes("function activeShareRoot()"));
assert.ok(from > 0 && to > from, "无法在脚本中定位 dom.js 工具栏区段");
const code = lines.slice(from, to).join("\n");

// —— 最小假 DOM：tag / .class / [attr] / [attr="v"]、:scope > 子代、dataset、classList ——
class El {
  constructor(tag, options = {}, children = []) {
    this.tagName = String(tag).toUpperCase();
    this.className = options.class || "";
    this.attrs = { ...(options.attrs || {}) };
    if (this.className) this.attrs.class = this.className;
    this.text = options.text || "";
    this.hidden = Boolean(options.hidden);
    this.parent = null;
    this.children = [];
    this.dataset = {};
    for (const [key, value] of Object.entries(options.dataset || {})) this.dataset[key] = value;
    for (const child of children) this.append(child);
  }
  append(child) { child.parent = this; this.children.push(child); return this; }
  get parentElement() { return this.parent; }
  getClientRects() { return this.hidden ? [] : [{ width: 60, height: 32 }]; }
  getAttribute(name) {
    if (name === "class") return this.className || null;
    return name in this.attrs ? String(this.attrs[name]) : null;
  }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  get classList() {
    const self = this;
    return { contains: (name) => String(self.className || "").split(/\s+/).includes(name) };
  }
  descendants() { const out = []; const walk = (node) => { for (const child of node.children) { out.push(child); walk(child); } }; walk(this); return out; }
  matchesSimple(selector) {
    return selector.split(",").map((part) => part.trim()).filter(Boolean).some((part) => {
      if (part.startsWith(":scope >")) return this.parent && this.className.split(/\s+/).includes(part.slice(8).trim().replace(/^\./, "")) && this.parent.children.includes(this);
      if (part.startsWith(".")) return this.className.split(/\s+/).includes(part.slice(1));
      if (part.startsWith("[") && part.endsWith("]")) {
        const body = part.slice(1, -1);
        const eq = body.indexOf("=");
        const rawName = eq < 0 ? body : body.slice(0, eq);
        const want = eq < 0 ? null : body.slice(eq + 1).replace(/^"|"$/g, "");
        // data-x-y 属性对应 dataset 里的 xY（脚本读写都走 dataset）
        const camel = rawName.startsWith("data-") ? rawName.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase()) : rawName;
        const value = camel in this.dataset ? this.dataset[camel] : this.attrs[rawName];
        if (value === undefined) return false;
        return want === null || String(value) === want;
      }
      return this.tagName === part.toUpperCase();
    });
  }
  querySelector(selector) { return this.descendants().find((node) => node.matchesSimple(selector)) || null; }
  querySelectorAll(selector) { return this.descendants().filter((node) => node.matchesSimple(selector)); }
  closest(selector) { let node = this; while (node) { if (node.matchesSimple(selector)) return node; node = node.parent; } return null; }
}
const button = (label, extra = {}) => new El("button", { text: label, ...extra });
let currentRoot = null;
const sandbox = {
  console, Set, Map, RegExp, JSON, Math, Number, String, Array, Object, Boolean, Error, Symbol, Intl,
  getComputedStyle: () => ({ display: "flex", visibility: "visible" }),
  document: { querySelectorAll: (selector) => (currentRoot ? currentRoot.querySelectorAll(selector) : []) }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`${code}
globalThis.__dom = { officialSelectedGroup, configureOfficialOverflow, markOfficialBarState, officialBarReady, officialBarBlank, clearOfficialOverflowControls, OFFICIAL_FOLD_ACTIONS, officialPlainBarPresent };`, sandbox, { filename: "123-helper.user.js" });
const { officialSelectedGroup, configureOfficialOverflow, markOfficialBarState, officialBarReady, officialBarBlank, clearOfficialOverflowControls, OFFICIAL_FOLD_ACTIONS, officialPlainBarPresent } = sandbox.__dom;

// 官方选中条现状（2026-10-01 实测的按钮集合）
const selectedGroup = () => new El("div", { class: "file-operator-group" }, [
  button("下载"), button("分享"), button("收藏"), button("批量重命名"), button("删除"),
  button("移动"), button("复制"), button("启用直链空间"), button("移入保险箱"), button("导出目录树"), button("更多")  // 2026-10-01 实测官方选中条按钮集合
]);
const plainGroup = () => new El("div", { class: "home-operator-button-group" }, [
  button("上传"), button("新建"), button("离线下载"), selectedGroup()
]);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test("折叠只认固定白名单：下载/收藏/复制/直链空间/保险箱/目录树收进更多，移动留在条上", () => {
  const page = plainGroup();
  currentRoot = page;
  const group = officialSelectedGroup(page);
  assert.ok(group, "应能定位官方选中条");
  const folded = configureOfficialOverflow(group, true);
  
  const hiddenLabels = group.children.filter((child) => child.dataset.c123ToolbarOverflow === "true").map((child) => child.textContent);
  const expectedFold = ["下载", "收藏", "复制", "启用直链空间", "移入保险箱", "导出目录树"].sort().join(",");
  assert.equal(hiddenLabels.slice().sort().join(","), expectedFold, `实际收起：${hiddenLabels}`);
  assert.equal(folded.slice().sort().join(","), hiddenLabels.slice().sort().join(","), "返回的收起清单应与实际一致（供「更多」菜单列项）");
  for (const keep of ["分享", "移动", "批量重命名", "删除", "更多"]) {
    assert.ok(!hiddenLabels.includes(keep), `${keep} 不该被收起`);
  }
});

test("白名单不含新增动作：官方日后加按钮默认原样显示，不被顺手藏掉", () => {
  const group = new El("div", { class: "file-operator-group" }, [button("下载"), button("全新官方动作")]);
  currentRoot = group;
  configureOfficialOverflow(group, true);
  assert.equal(group.children[1].dataset.c123ToolbarOverflow, undefined, "白名单外的动作不折叠");
});

test("没有官方选中条时清掉全部折叠标记（取消勾选不留残留）", () => {
  const page = new El("div", { class: "home-operator-button-group" }, [button("上传"), button("新建")]);
  const stale = button("移动");
  page.append(stale);
  stale.dataset.c123ToolbarOverflow = "true";
  currentRoot = page;
  clearOfficialOverflowControls();
  assert.equal(stale.dataset.c123ToolbarOverflow, undefined, "应清掉失效的折叠标记");
});

test("选中条在场时打 bar-selected / official-rename 标记，缺席时复位", () => {
  const page = plainGroup();
  const group = officialSelectedGroup(page);
  markOfficialBarState(page, group);
  assert.equal(page.dataset.c123BarSelected, "true", "选中条在场");
  assert.equal(page.dataset.c123OfficialRename, "true", "官方自带批量重命名时标记为真");
  markOfficialBarState(page, null);
  assert.equal(page.dataset.c123BarSelected, "false", "无选中条时复位");
  assert.equal(page.dataset.c123OfficialRename, "false", "无选中条时助手重命名照常显示");
});

test("官方选中条里没有重命名时不打 official-rename（助手那颗要能点）", () => {
  const group = new El("div", { class: "file-operator-group" }, [button("下载"), button("分享"), button("删除")]);
  const page = new El("div", { class: "home-operator-button-group" }, [button("上传"), group]);
  markOfficialBarState(page, group);
  assert.equal(page.dataset.c123BarSelected, "true");
  assert.equal(page.dataset.c123OfficialRename, "false");
});

test("助手自己的节点不算官方按钮（不会被当成选中条或重命名）", () => {
  const helper = new El("span", { class: "c123-helper-toolbar", dataset: { cloud123Helper: "toolbar" } }, [button("重命名"), button("整理")]);
  const page = new El("div", { class: "home-operator-button-group" }, [button("上传"), helper]);
  assert.equal(officialSelectedGroup(page), null, "只有助手按钮时不该判成官方选中条");
  markOfficialBarState(page, null);
  assert.equal(page.dataset.c123OfficialRename, "false", "助手的重命名不该让官方那颗被误认存在");
});

test("官方空壳测量阶段：选中条在场但按钮不可见 → 不算就绪、不折叠", () => {
  const shell = new El("div", { class: "file-operator-group" }, [
    button("下载", { hidden: true }), button("分享", { hidden: true }), button("删除", { hidden: true }),
    // 真实 DOM 里 measuring-container 整体 visibility:hidden，子按钮也拿不到可见计算样式；
    // 沙箱的 getComputedStyle 是常量桩，这里显式把子按钮标成不可见来还原同一语义
    new El("div", { class: "measuring-container" }, [button("下载", { hidden: true }), button("分享", { hidden: true })])
  ]);
  const page = new El("div", { class: "home-operator-button-group" }, [shell]);
  currentRoot = page;
  markOfficialBarState(page, shell);
  assert.equal(page.dataset.c123BarReady, "false", "空壳阶段不该判成就绪（否则助手按钮会先于官方出现）");
  assert.equal(configureOfficialOverflow(shell, false).join(","), "", "未就绪时不折叠官方按钮");
});

test("兜底定位：官方改名没有 .file-operator-group 时按动作按钮聚类找出选中条", () => {
  const group = new El("div", { class: "whatever-2027" }, [button("分享"), button("收藏"), button("删除")]);
  const page = new El("div", { class: "home-operator-button-group" }, [button("上传"), group]);
  assert.equal(officialSelectedGroup(page), group, "应退回到动作聚类结果");
});

test("助手自己的按钮不参与官方折叠与选中条判定", () => {
  const helper = new El("span", { class: "c123-helper-toolbar", dataset: { cloud123Helper: "toolbar" } }, [button("下载"), button("移动")]);
  const group = new El("div", { class: "file-operator-group" }, [button("分享"), button("删除"), helper]);
  currentRoot = group;
  const folded = configureOfficialOverflow(group, true);
  
  assert.equal(folded.join(","), "", "助手节点里的同名文案不该被收编");
  assert.equal(helper.dataset.c123ToolbarOverflow, undefined, "助手节点不该被打折叠标记");
});

test("未选中条特征按钮判定：区分「官方还没换上新条」与「真的取消勾选」", () => {
  const plain = new El("div", { class: "home-operator-button-group" }, [button("上传"), button("新建"), button("离线下载")]);
  assert.equal(officialPlainBarPresent(plain), true);
  const onlyFog = new El("div", { class: "home-operator-button-group" }, [new El("div", { class: "file-operator-group" }, [button("分享"), button("删除")])]);
  assert.equal(officialPlainBarPresent(onlyFog), false, "只有选中条时不算未选中条在场");
  const vacuum = new El("div", { class: "home-operator-button-group" }, []);
  assert.equal(officialPlainBarPresent(vacuum), false, "两棵都不在（换装空档）→ 调用方沿用上一拍");
});

test("空白判定：选中条空壳阶段与换装空档都算空白，未选中条在场不算", () => {
  const shell = new El("div", { class: "file-operator-group" }, [button("下载", { hidden: true }), button("分享", { hidden: true })]);
  const shellPage = new El("div", { class: "home-operator-button-group" }, [shell]);
  currentRoot = shellPage;
  assert.equal(officialBarBlank(shellPage, shell), true, "空壳阶段：官方一颗可见按钮都没有");
  const empty = new El("div", { class: "home-operator-button-group" }, []);
  currentRoot = empty;
  assert.equal(officialBarBlank(empty, null), true, "两棵条都不在：换装空档");
  const plain = new El("div", { class: "home-operator-button-group" }, [button("上传"), button("新建"), button("离线下载")]);
  currentRoot = plain;
  assert.equal(officialBarBlank(plain, null), false, "未选中条在场：不干预官方自己的按钮");
  const settledFog = selectedGroup();
  const livePage = new El("div", { class: "home-operator-button-group" }, [settledFog]);
  currentRoot = livePage;
  assert.equal(officialBarBlank(livePage, settledFog), false, "选中条按钮已翻成可见：不算空白");
});

test("markOfficialBarState 会写 bar-blank 标记", () => {
  const shell = new El("div", { class: "file-operator-group" }, [button("下载", { hidden: true }), button("分享", { hidden: true })]);
  const page = new El("div", { class: "home-operator-button-group" }, [shell]);
  currentRoot = page;
  markOfficialBarState(page, shell);
  assert.equal(page.dataset.c123BarBlank, "true", "空壳阶段要打上空白标记");
  assert.equal(page.dataset.c123BarReady, "false", "空壳阶段同时不算就绪");
});

test("换装空档（两棵条都不在）沿用上一拍，不出现空白帧", () => {
  const group = selectedGroup();
  const page = new El("div", { class: "home-operator-button-group" }, [group]);
  currentRoot = page;
  markOfficialBarState(page, group);
  assert.equal(page.dataset.c123BarReady, "true", "先建立就绪态");
  // 空档 = 同一个 host 上选中条已被卸载、未选中条还没挂上来（新建节点没有旧状态可言）
  page.children = [];
  page.parent = null;
  currentRoot = page;
  markOfficialBarState(page, null);
  assert.equal(page.dataset.c123BarReady, "true", "官方换装空档沿用上一拍，条子不该整条空白");
  page.append(button("上传"));
  page.append(button("新建"));
  page.append(button("离线下载"));
  markOfficialBarState(page, null);
  assert.equal(page.dataset.c123BarReady, "false", "未选中条回来了才算真的取消勾选");
});

test("契约：助手工具栏只挂稳定父节点，绝不插进官方选中条", () => {
  assert.ok(source.includes("const toolbarHost = host;"), "挂载父节点应是稳定 host");
  assert.ok(!source.includes("officialGroup.insertBefore(toolbar"), "不得再把助手节点插进官方选中条");
  assert.ok(source.includes("if (toolbar.parentElement !== host) host.append(toolbar);"), "应只在脱离稳定父节点时补挂");
});

test("契约：显隐与置灰都看 data-c123-bar-ready，不用 :has() 直接放行", () => {
  for (const rule of [
    '.c123-helper-toolbar > [data-c123-bar-gate="true"] { display:none !important; }',
    '.home-operator-button-group[data-c123-bar-ready="true"] .c123-helper-toolbar > [data-c123-bar-gate="true"] { display:inline-flex !important; }',
    '.home-operator-button-group[data-c123-official-rename="true"] .c123-helper-toolbar > [data-c123-bar-gate="true"][data-command="rename"] { display:none !important; }',
    '.home-operator-button-group[data-c123-bar-ready="true"] > .c123-compact-more { display:none !important; }'
  ]) assert.ok(source.includes(rule), `缺少 CSS 规则：${rule}`);
  assert.ok(!source.includes(":has(.file-operator-group) .c123-helper-toolbar > [data-c123-bar-gate"), "不该再用 :has() 放行助手按钮（会在空壳阶段早一拍）");
  assert.ok(source.includes('const gate = requiresSelection && command !== "fastlinkImport"'), "「转存秒传」不该被 CSS 门控接管（还要判种子文件）");
});

test("未就绪不折叠：空壳阶段先不动官方按钮", () => {
  // 空壳阶段：真实按钮还没翻成可见（沙箱里用 hidden 还原），只有 measuring 副本在量宽
  const shell = new El("div", { class: "file-operator-group" }, [
    button("下载", { hidden: true }), button("分享", { hidden: true }), button("删除", { hidden: true })
  ]);
  const page = new El("div", { class: "home-operator-button-group" }, [shell]);
  currentRoot = page;
  markOfficialBarState(page, shell);
  assert.equal(page.dataset.c123BarReady, "false", "空壳阶段不该算就绪");
  assert.equal(configureOfficialOverflow(shell, false).join(","), "", "未就绪时不折叠（折叠时机跟着 bar-ready 走）");
});

test("契约：绝不给官方选中条改宽度（它是官方溢出计算的基准）", () => {
  assert.ok(!/\.file-operator-group\s*\{[^}]*width:/.test(source), "给选中条设 width 会让官方把整排按钮折进「更多」（实测踩过，别再试）");
});

test("契约：助手组恒定一个盒子，位置只由 order 决定且不重复", () => {
  assert.ok(source.includes(".home-operator-button-group > .c123-helper-toolbar { display:inline-flex; align-items:stretch; order:999; flex:0 0 auto; }"), "助手组应恒为 inline-flex 盒子（未选中排官方之后）");
  assert.ok(source.includes('.home-operator-button-group[data-c123-bar-ready="true"] > .c123-helper-toolbar { order:-1; margin-right:-16px; }'), "就绪后整块贴到官方条左侧并抵掉容器 gap");
  assert.ok(!source.includes(".home-operator-button-group > .c123-helper-toolbar { display:contents; }"), "不该用 display:contents（会被容器 16px gap 拆成散点，切盒子模型还多一次重排）");
  // 位置恒定：助手组两态都排在官方按钮之后，不许再随勾选左右换位（换位＝维护者看到的「往左缩进」）
  // 两态同一个 order：不许再出现「未选中排最右、选中跳最左」的换位（那就是往左缩进）
  // 「往左缩」是已知取舍（维护者 2026-10-01 定：保留、不再优化）；两态各一个 order 是有意为之
  assert.ok(!source.includes("[data-c123-official-bar-hidden"), "不应再整条隐藏官方选中条（已撤销自绘方案）");
  for (const rule of [
    '.home-operator-button-group[data-c123-bar-blank="true"] > .c123-helper-toolbar,',
    '.home-operator-button-group[data-c123-bar-blank="true"] > .c123-compact-more { display:none !important; }'
  ]) assert.ok(source.includes(rule), `缺少空白期隐藏规则：${rule}`);
});

test("契约：折叠与标记在 MutationObserver 同步阶段跑，属性变化也要触发", () => {
  const start = lines.findIndex((line) => line.includes("this.observer = new MutationObserver("));
  const end = lines.findIndex((line, index) => index > start && line.includes("this.observer.observe("));
  const block = lines.slice(start, end).join("\n");
  assert.ok(start > 0 && block.includes("this.syncOfficialBarLayout()"), "MO 回调里应同步跑排版");
  assert.ok(block.includes('mutation.type === "attributes"'), "官方翻牌改的是 class/style，只听 addedNodes 会漏那一帧");
  assert.ok(block.includes("this.syncPage()"), "重活仍走防抖 syncPage");
  const ensureStart = lines.findIndex((line) => line.includes("ensureFileToolbar() {"));
  const ensure = lines.slice(ensureStart, ensureStart + 30).join("\n");
  assert.ok(ensure.includes('host.dataset.c123BarReady === "true" ? Boolean(officialGroup) : false'), "折叠跟着 bar-ready 走：官方条没翻出可见按钮前不动它的按钮");
});

test("契约：观察者跳过助手子树自写，杜绝「写→观察者→再写」自反馈空转", () => {
  const start = lines.findIndex((line) => line.includes("this.observer = new MutationObserver("));
  const end = lines.findIndex((line, index) => index > start && line.includes("this.observer.observe("));
  const block = lines.slice(start, end).join("\n");
  assert.ok(start > 0 && end > start, "应能定位观察者回调");
  assert.ok(block.includes('closest?.("[data-cloud123-helper]")'), "助手子树里的自写变更必须跳过（hidden 等也在监听属性里，不跳过就会自己喂自己）");
  assert.ok(block.includes("if (!relevant) return;"), "整批都是自写时直接返回，不再无条件 syncPage");
});

test("契约：updateToolbarState 写入带值守卫（同值重写也会产生 MutationRecord）", () => {
  const start = lines.findIndex((line) => line.trim().startsWith("updateToolbarState() {"));
  const end = lines.findIndex((line, index) => index > start && line.trim().startsWith("toast(message, type"));
  const state = lines.slice(start, end).join("\n");
  assert.ok(start > 0 && end > start, "应能定位 updateToolbarState");
  assert.ok(state.includes("if (element && element.hidden !== next) element.hidden = next;"), "hidden 写入必须带值守卫");
  assert.ok(state.includes('setDataFlag(this.toolbar, "hasSelection"'), "hasSelection 等标记写入也要走守卫");
  assert.ok(!/^\s*seedButton\.hidden = /m.test(state), "不该再有裸写的 seedButton.hidden");
  assert.ok(!/^\s*this\.toolbar\.hidden = /m.test(state), "不该再有裸写的 toolbar.hidden");
  assert.ok(!/^\s*seedButton\.style\.opacity = "";/m.test(state), "过渡样式清除也要先判空");
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
console.log(`\nofficial-toolbar-mount: 全部 ${cases.length} 个用例通过`);
