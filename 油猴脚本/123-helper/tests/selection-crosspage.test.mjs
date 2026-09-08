// 跨页/翻页选中集修正（reconcileSelectionSnapshot）回归测试。
// 背景：123 文件表格一页只渲染约 30 行，旧逻辑会把"不在可见行"的选中 id
// 直接剔除，导致勾选/全选 200+ 文件后批量重命名只剩当前页的约 30 个。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const lines = readFileSync(join(root, "123-helper.user.js"), "utf8").split("\n");
const start = lines.findIndex((line) => line.includes("function reconcileSelectionSnapshot"));
const end = lines.findIndex((line) => line.includes("var PageBridge = class"));
assert.ok(start > 0 && end > start, "无法在脚本中定位 reconcileSelectionSnapshot");
const context = vm.createContext({});
vm.runInContext(lines.slice(start, end).join("\n"), context);
const reconcile = context.reconcileSelectionSnapshot;
assert.equal(typeof reconcile, "function");

function run(state, visibleRows, hostCount) {
  reconcile(state, new Map(visibleRows), hostCount);
  return state;
}

function makeState(selected = [], unselected = [], selectAll = false) {
  return { selectAll, selectedIds: new Set(selected), unselectedIds: new Set(unselected) };
}

// 1. 跨页手动勾选：第 1 页勾了 a/b/c，翻到第 2 页勾 d/e，宿主计数 5。
// 旧逻辑会把 a/b/c 当"不可见"剔掉，只剩当前页。
{
  const state = run(makeState(["a", "b", "c"]), [["d", true], ["e", true]], 5);
  assert.deepEqual([...state.selectedIds].sort(), ["a", "b", "c", "d", "e"]);
  assert.equal(state.selectAll, false);
}

// 2. 跨页全选升级：宿主计数(200)大于累计选中数(2)且当前页全部勾选
// → 升级为 selectAll（表头全选框未被脚本识别到时的兜底）。
{
  const state = run(makeState(["d", "e"]), [["d", true], ["e", true]], 200);
  assert.equal(state.selectAll, true);
  assert.equal(state.selectedIds.size, 0);
  assert.equal(state.unselectedIds.size, 0);
}

// 3. 翻页中间态：第 1 页勾了 3 个，刚翻到第 2 页（全部未勾选），计数仍为 3。
{
  const state = run(makeState(["a", "b", "c"]), [["d", false], ["e", false]], 3);
  assert.deepEqual([...state.selectedIds].sort(), ["a", "b", "c"]);
}

// 4. 单页全勾不升级：计数(30)等于累计数(30)，不能误判为全选。
{
  const rows = Array.from({ length: 30 }, (_, index) => [`id${index}`, true]);
  const state = run(makeState(), rows, 30);
  assert.equal(state.selectAll, false);
  assert.equal(state.selectedIds.size, 30);
}

// 5. 删除部分选中文件：计数(20)小于累计数(50) → 不可见的陈旧 id 被剔除。
{
  const state = run(makeState([...Array.from({ length: 50 }, (_, index) => `old${index}`)]), [["keep1", true], ["keep2", true]], 20);
  assert.deepEqual([...state.selectedIds].sort(), ["keep1", "keep2"]);
}

// 6. 删除全部选中文件且计数已归零：不可见 id 全部剔除。
{
  const state = run(makeState(["a", "b", "c"]), [], 0);
  assert.equal(state.selectedIds.size, 0);
}

// 6b. 删除后计数滞后（仍显示旧值）：保留选中，等计数归零再清，避免误伤翻页勾选。
{
  const state = run(makeState(["a", "b", "c"]), [], 3);
  assert.deepEqual([...state.selectedIds].sort(), ["a", "b", "c"]);
}

// 7. selectAll 模式翻页取消勾选：unselectedIds 跨页累积，不被剔除。
{
  const state = run(makeState([], ["x"], true), [["d", false], ["e", false]], 200);
  assert.equal(state.selectAll, true);
  assert.deepEqual([...state.unselectedIds].sort(), ["d", "e", "x"]);
}

// 8. 找不到宿主计数文本（hostCount=null）时保留已累积的选中 id（参考 123FastRename 语义）。
{
  const state = run(makeState(["a", "ghost"]), [["a", true]], null);
  assert.deepEqual([...state.selectedIds].sort(), ["a", "ghost"]);
}

// 9. 删除部分选中文件后计数从 50 修正到 30：不可见的 20 个陈旧 id 被剔除。
{
  const state = run(
    makeState([...Array.from({ length: 50 }, (_, index) => `old${index}`)]),
    [["old0", true], ["old1", true]],
    30
  );
  assert.deepEqual([...state.selectedIds].sort(), ["old0", "old1"]);
}

console.log("selection-crosspage: all", 10, "cases passed");
