// 工具栏「秒传」常驻按钮回归测试。
// 背景：1.3.2 起「秒传」与「更多」按钮的显隐/交互条件对齐——工具栏在即常驻显示、恒可点，
// 不随勾选出现/消失，也不参与官方溢出收编。
// 1.4.5 位置改版：助手按钮改挂稳定父节点 .home-operator-button-group，位置由 order 决定
// （未选中排官方按钮之后、官方选中条在场时整组挪到最左），「秒传」按 DOM 顺序紧跟「整理」，
// 不再单独钉到「更多」旁边。
// mountToolbar/updateToolbarState 是 DOM 逻辑，这里按仓库惯例从脚本里切出方法体，
// 在 node:vm 里用桩 toolbar 驱动，另做源码契约断言（挂载参数与定位样式不被误删）。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, "123-helper.user.js"), "utf8");
const lines = source.split("\n");

// 1. 挂载契约：fastlink 以 requiresSelection=false + data-c123-pin-more 挂载（不带勾选要求）。
{
  const start = lines.findIndex((line) => line.includes("mountToolbar(container, context = {})"));
  const end = lines.findIndex((line, index) => index > start && line.trim().startsWith("runToolbarCommand(control, event)"));
  assert.ok(start > 0 && end > start, "无法在脚本中定位 mountToolbar");
  const mount = lines.slice(start, end).join("\n");
  assert.ok(
    mount.includes(`["fastlink", "\\u79D2\\u4F20", false, ' data-c123-pin-more="true"']`),
    "fastlink 条目必须是 requiresSelection=false 且携带 data-c123-pin-more"
  );
  const fastlinkEntry = mount.split('["fastlink",')[1].split("]")[0];
  assert.ok(!fastlinkEntry.includes("true,"), "fastlink 不应再要求勾选（requiresSelection 必须为 false）");
}

// 2. 样式契约：常驻按钮挂在稳定父节点下、排官方按钮之后，且 flex:0 0 auto 不被挤压。
assert.ok(
  source.includes(".home-operator-button-group > .c123-helper-toolbar { display:inline-flex; align-items:stretch; order:999; flex:0 0 auto; }"),
  "缺少未选中态助手按钮组的定位样式"
);
assert.ok(
  source.includes('officialMore.style.order = "1002"'),
  "官方「更多」需要内联 order:1002，秒传(999)才能排它左侧"
);

// 2b. 紧凑「更多」切换钮不再借用官方 .more 变体（分组外渲染会缺边框），改为复制参考按钮 class。
assert.ok(
  !source.includes('"file-operator-group-button more c123-compact-more-toggle"'),
  "紧凑「更多」不应再硬编码 .more 变体 class"
);
assert.ok(
  source.includes("const toggleClassName = reference?.getAttribute(\"class\") || \"file-operator-group-button\";"),
  "紧凑「更多」应复制同容器参考按钮的 class"
);

// 3. 行为：updateToolbarState 的显隐矩阵。
const start = lines.findIndex((line) => line.trim().startsWith("updateToolbarState() {"));
const end = lines.findIndex((line) => line.trim().startsWith("toast(message, type"));
assert.ok(start > 0 && end > start, "无法在脚本中定位 updateToolbarState");
const context = vm.createContext({
  readTableSelectionRecords: () => [],
  isSeedLikeName: () => false
});
const { updateToolbarState } = vm.runInContext("({" + lines.slice(start, end).join("\n") + "})", context);
assert.equal(typeof updateToolbarState, "function");

function makeButton(extra = {}) {
  return {
    disabled: false,
    hidden: false,
    dataset: {},
    style: {},
    attrs: {},
    title: "",
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    getAttribute(name) {
      return name in this.attrs ? this.attrs[name] : null;
    },
    ...extra
  };
}

function makeToolbar({ withPin }) {
  const pin = withPin ? makeButton({ dataset: { command: "fastlink", c123PinMore: "true" } }) : null;
  const seed = makeButton({ dataset: { command: "fastlinkImport" } });
  // 1.4.5：重命名/整理带 bar-gate（显隐与置灰都交给 CSS/官方条同帧）；再放一颗不带标记的，
  // 代表分享页工具栏那类仍由 JS 控制的老按钮。
  const selectionButtons = [makeButton({ dataset: { c123BarGate: "true" } }), makeButton({ dataset: { c123BarGate: "true" } }), makeButton({ legacy: true })];
  const legacyButtons = [selectionButtons[2]];
  return {
    pin,
    seed,
    selectionButtons,
    legacyButtons,
    hidden: false,
    dataset: {},
    querySelector(selector) {
      if (selector.includes("data-c123-pin-more")) return this.pin;
      if (selector.includes("fastlinkImport")) return this.seed;
      return null;
    },
    querySelectorAll(selector) {
      if (selector.includes("data-requires-selection")) return this.selectionButtons;
      return [];
    }
  };
}

function run(toolbar, selection) {
  updateToolbarState.call({ toolbar, selection });
}

// 3a. 无勾选 + 常驻秒传：工具栏保持显示（与「更多」一致），秒传按钮不被禁用/隐藏。
{
  const toolbar = makeToolbar({ withPin: true });
  run(toolbar, { hasSelection: false, selectAll: false, selectedIds: new Set() });
  assert.equal(toolbar.hidden, false, "无勾选时常驻秒传应让工具栏保持显示");
  assert.equal(toolbar.pin.hidden, false, "秒传按钮不应被隐藏");
  assert.equal(toolbar.pin.disabled, false, "秒传按钮不应被禁用");
  // 1.4.5 第二轮：bar-gate 按钮既不写 hidden 也不写 disabled —— 出现与否由 CSS 跟官方选中条同帧决定，
  // JS 快照迟到时写置灰就会「先灰一下再变黑」（维护者截图里的闪）。
  const gated = toolbar.selectionButtons.filter((button) => button.dataset.c123BarGate === "true");
  assert.equal(gated.length, 2, "夹具里应有两颗 bar-gate 按钮");
  assert.ok(gated.every((button) => !button.disabled), "bar-gate 按钮不该被 JS 置灰");
  assert.ok(gated.every((button) => !button.hidden), "bar-gate 按钮的显隐交给 CSS，JS 不写 hidden");
  assert.ok(gated.every((button) => button.attrs["aria-disabled"] === "false"), "aria-disabled 也保持可用");
  assert.equal(toolbar.seed.hidden, true, "种子按钮无勾选时仍应隐藏");
  assert.ok(toolbar.legacyButtons.every((button) => button.hidden && button.disabled), "不带 bar-gate 的老按钮保持原语义：无勾选时隐藏并置灰");
}

// 3b. 无勾选 + 无常驻秒传：保留旧行为，工具栏整体隐藏。
{
  const toolbar = makeToolbar({ withPin: false });
  run(toolbar, { hasSelection: false, selectAll: false, selectedIds: new Set() });
  assert.equal(toolbar.hidden, true, "无常驻按钮时无勾选仍应隐藏工具栏");
}

// 3c. 勾选 1 个普通文件（非种子）：工具栏显示，勾选类按钮恢复，种子按钮仍隐藏。
{
  const toolbar = makeToolbar({ withPin: true });
  run(toolbar, { hasSelection: true, selectAll: false, selectedIds: new Set(["1"]) });
  assert.equal(toolbar.hidden, false);
  assert.ok(toolbar.selectionButtons.every((button) => !button.disabled), "勾选后勾选类按钮应恢复可用");
  assert.equal(toolbar.seed.hidden, true, "非种子文件不应显示「转存秒传」");
}

// 3d. 全选：种子按钮隐藏（selectAll 不算单选种子）。
{
  const toolbar = makeToolbar({ withPin: true });
  run(toolbar, { hasSelection: true, selectAll: true, selectedIds: new Set() });
  assert.equal(toolbar.hidden, false);
  assert.equal(toolbar.seed.hidden, true);
}

console.log("toolbar-fastlink-pin: 全部断言通过");
