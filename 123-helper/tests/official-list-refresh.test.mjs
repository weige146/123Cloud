// 官方「刷新列表和直链缓存」按钮定位回归（2026-09-30 起因官方新增「容量刷新」而修）。
//
// 背景：官方在左侧「云盘空间」卡片新增了一个「容量刷新」按钮（tooltip「容量刷新」，图标名与列表
// 刷新同为 general_refresh，且 DOM 顺序排在列表刷新前面），点它会弹「手动刷新容量」确认框并触发
// 容量重算接口。脚本旧写法按图标名全局取第一个 → 整理/重命名/分享等操作后的正常刷新点成了容量刷新，
// 每次操作完都弹窗。这里用最小假 DOM 驱动 findOfficialListRefreshButton 与
// dismissManualCapacityRefreshDialog，钉住「只点列表刷新、误弹的容量框自动取消、绝不点刷新容量」。
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const source = fs.readFileSync(fileURLToPath(new URL("../123-helper.user.js", import.meta.url)), "utf8");
const lines = source.split("\n");
const from = lines.findIndex((line) => line.includes("var SELECTORS = {"));
const to = lines.findIndex((line) => line.includes("function activeShareRoot()"));
assert.ok(from > 0 && to > from, "无法在脚本中定位 dom.js 的按钮选择器区段");
const code = lines.slice(from, to).join("\n");

// —— 最小假 DOM：支持 tag / .class / [attr] / [attr="v"]、逗号组与空格后代 ——
function parseAtom(text) {
  if (text.startsWith("[")) {
    const withValue = /^\[([a-zA-Z][\w-]*)([*^$|~]?=)"((?:[^"\\]|\\.)*)"\]$/.exec(text);
    if (withValue) return { attr: withValue[1], attrValue: withValue[3], exact: withValue[2] === "=" };
    const flagOnly = /^\[([a-zA-Z][\w-]*)\]$/.exec(text);
    if (flagOnly) return { attr: flagOnly[1] };
    return { attr: text.slice(1, -1) };
  }
  if (text.startsWith(".")) return { cls: text.slice(1) };
  return { tag: text };
}
function parseSelector(selector) {
  // 逗号组 × 空格后代；链按「节点自身 → 祖先」倒序存，svg use 这类才匹配得对
  return selector.split(",").map((part) => part.trim().split(/\s+/).filter(Boolean).map(parseAtom).reverse()).filter((chain) => chain.length);
}
function matchesAtom(node, atom) {
  if (atom.attr) {
    const value = node.getAttribute(atom.attr);
    if (value === null) return false;
    if (atom.attrValue === undefined) return true;
    return atom.exact ? String(value) === atom.attrValue : String(value).includes(atom.attrValue);
  }
  if (atom.cls) return String(node.className || "").split(/\s+/).includes(atom.cls);
  return node.tagName === atom.tag.toUpperCase();
}
function matchesChain(node, chain) {
  if (!matchesAtom(node, chain[0])) return false;
  if (chain.length === 1) return true;
  let parent = node.parent;
  while (parent) {
    if (matchesChain(parent, chain.slice(1))) return true;
    parent = parent.parent;
  }
  return false;
}
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
    this.clicks = 0;
    for (const child of children) this.append(child);
  }
  append(child) { child.parent = this; this.children.push(child); return this; }
  getAttribute(name) { return name in this.attrs ? String(this.attrs[name]) : null; }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  click() { this.clicks += 1; }
  descendants() { const out = []; const walk = (node) => { for (const child of node.children) { out.push(child); walk(child); } }; walk(this); return out; }
  querySelectorAll(selector) { const chains = parseSelector(selector); return this.descendants().filter((node) => chains.some((chain) => matchesChain(node, chain))); }
  closest(selector) { const chains = parseSelector(selector); let node = this; while (node) { if (chains.some((chain) => matchesChain(node, chain))) return node; node = node.parent; } return null; }
}
const refreshIcon = (href) => new El("svg", {}, [new El("use", { attrs: { "xlink:href": href } })]);
// 侧栏「云盘空间」卡片里的官方容量刷新按钮（图标同名，DOM 顺序在前）
const capacityArea = () => new El("div", { class: "spaceCard_niEid" }, [
  new El("div", { class: "totalUsage_YbWkt" }, [
    new El("span", { class: "refreshWrap_y0Szj" }, [
      new El("div", { class: "mfy-tooltip" }, [
        new El("button", { class: "refreshButton_lxxJw" }, [refreshIcon("#general_refresh_16_1")])
      ])
    ])
  ])
]);
// 文件列表操作区右上角的「刷新列表和直链缓存」
const listRefreshArea = (options = {}) => new El("div", { class: "home-operator" }, [
  new El("div", { class: "right-operator" }, [
    new El("div", { class: "layout-operate-icon", ...options }, [refreshIcon("#general_refresh_24_1")])
  ])
]);

const sandbox = { console, Set, Map, RegExp, JSON, Math, Number, String, Array, Object, Boolean, Error, Symbol, Intl };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`${code}\nglobalThis.__dom = { findOfficialListRefreshButton, dismissManualCapacityRefreshDialog, CAPACITY_REFRESH_SCOPE };`, sandbox, { filename: "123-helper.user.js" });
const { findOfficialListRefreshButton, dismissManualCapacityRefreshDialog, CAPACITY_REFRESH_SCOPE } = sandbox.__dom;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test("官方现状：侧栏容量刷新排在前面，也只点列表操作区的刷新按钮", () => {
  const page = new El("div", {}, [capacityArea(), listRefreshArea()]);
  const capacityButton = page.querySelectorAll('button[class*="refreshButton"]')[0];
  const picked = findOfficialListRefreshButton(page);
  assert.equal(picked.className, "layout-operate-icon", "应选列表刷新按钮");
  assert.notEqual(picked, capacityButton, "绝不能选侧栏容量刷新");
});

test("只有侧栏容量刷新时返回 null，不拿它当列表刷新点", () => {
  assert.equal(findOfficialListRefreshButton(new El("div", {}, [capacityArea()])), null);
});

test("旧布局兜底：没有 .layout-operate-icon 时认非容量区的图标按钮", () => {
  const legacy = new El("div", {}, [
    capacityArea(),
    new El("div", {}, [new El("button", { class: "old-refresh" }, [refreshIcon("#general_refresh")])])
  ]);
  assert.equal(findOfficialListRefreshButton(legacy)?.className, "old-refresh", "图标挂在普通按钮上时仍可兜底");
});

test("带标注的官方按钮优先（aria-label 命中即用它，不看图标）", () => {
  const labelled = new El("div", { attrs: { "aria-label": "刷新列表和直链缓存" } });
  const page = new El("div", {}, [capacityArea(), labelled, listRefreshArea()]);
  assert.equal(findOfficialListRefreshButton(page), labelled);
});

test("列表刷新按钮隐藏时不点它，也不退回去点容量刷新", () => {
  const page = new El("div", {}, [capacityArea(), listRefreshArea({ hidden: true })]);
  assert.equal(findOfficialListRefreshButton(page), null);
});

test("容量排除名单覆盖官方新卡片的类名", () => {
  for (const part of ["spaceCard", "totalUsage", "refreshWrap", "refreshButton"]) {
    assert.ok(CAPACITY_REFRESH_SCOPE.includes(part), `排除名单缺 ${part}`);
  }
});

test("误弹的「手动刷新容量」框自动点取消，且绝不点「刷新容量」", () => {
  const cancel = new El("button", { class: "mfy-button default", text: "取消" });
  const confirm = new El("button", { class: "mfy-button primary", text: "刷新容量" });
  const page = new El("div", {}, [new El("div", { class: "mfy_h-modal-module__modal__cU-py" }, [
    new El("div", { class: "header", text: "手动刷新容量" }),
    new El("div", { class: "body", text: "如云盘已用容量显示不准确，可点击下方「刷新容量」按钮，系统将重新计算并更新容量，请耐心等待。" }),
    cancel,
    confirm
  ])]);
  assert.equal(dismissManualCapacityRefreshDialog(page), true);
  assert.equal(cancel.clicks, 1, "点一次取消");
  assert.equal(confirm.clicks, 0, "绝不调官方刷新容量");
});

test("没有取消按钮时点关闭叉；不是这个框时不动别人的弹窗", () => {
  const close = new El("button", { class: "mfy_h-modal-module__close__AQKal" });
  const page = new El("div", {}, [new El("div", { class: "modal" }, [new El("div", { text: "手动刷新容量" }), close])]);
  assert.equal(dismissManualCapacityRefreshDialog(page), true);
  assert.equal(close.clicks, 1);
  const otherCancel = new El("button", { text: "取消" });
  const other = new El("div", {}, [new El("div", { class: "modal" }, [new El("div", { text: "重命名" }), otherCancel])]);
  assert.equal(dismissManualCapacityRefreshDialog(other), false, "别的弹窗不碰");
  assert.equal(otherCancel.clicks, 0);
});

test("接线契约：刷新走统一定位函数，误弹兜底不被删", () => {
  const bundle = lines.join("\n");
  assert.ok(bundle.includes("const button = findOfficialListRefreshButton(document);"), "refresh() 必须用统一定位函数");
  assert.ok(bundle.includes("setTimeout(() => dismissManualCapacityRefreshDialog(document), 250);"), "刷新后要带误弹自动取消的兜底");
  assert.ok(!bundle.includes("/general_refresh(?:_24)?/"), "不得再按图标名全局取第一个（会点成容量刷新）");
  assert.ok(bundle.includes("!element.closest(CAPACITY_REFRESH_SCOPE)"), "图标兜底必须排除侧栏容量卡片");
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`  ok ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  FAIL ${name}`);
    console.log(String(error?.stack || error).split("\n").slice(0, 5).map((line) => `      ${line}`).join("\n"));
  }
}
console.log(failed === 0 ? `\n全部 ${cases.length} 个用例通过` : `\n${failed}/${cases.length} 个用例失败`);
process.exit(failed === 0 ? 0 : 1);
