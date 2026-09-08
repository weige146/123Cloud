// 选中项出口按文件名自然排序（sortItemsByName）回归测试。
// 背景：勾选集合按"首次观察到勾选"的先后插入，先点第 23 集再 shift 点第 1 集
// 时顺序为 [23, 1..22]，批量重命名预览与编号类规则（{n}/序号）会跟着点击顺序走。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const lines = readFileSync(join(root, "123-helper.user.js"), "utf8").split("\n");
const start = lines.findIndex((line) => line.includes("var ITEM_NAME_COLLATOR"));
const end = lines.findIndex((line) => line.includes("function readTableSelectionRecords"));
assert.ok(start > 0 && end > start, "无法在脚本中定位 sortItemsByName");
const context = vm.createContext({});
vm.runInContext(lines.slice(start, end).join("\n"), context);
const sortItemsByName = context.sortItemsByName;
assert.equal(typeof sortItemsByName, "function");

function names(items) {
  return items.map((item) => item.name);
}

// 1. 复现线上场景：先勾 S03E23 再 shift 勾到 S03E01，选中顺序为 23, 1..22。
{
  const order = ["S03E23", ...Array.from({ length: 22 }, (_, index) => `S03E${String(index + 1).padStart(2, "0")}`)];
  const sorted = sortItemsByName(order.map((name) => ({ id: name, name })));
  assert.deepEqual(names(sorted), Array.from({ length: 23 }, (_, index) => `S03E${String(index + 1).padStart(2, "0")}`));
}

// 2. 未补零的编号也要按数值排序：E2 排在 E10 前面。
{
  const sorted = sortItemsByName([{ name: "E10" }, { name: "E2" }, { name: "E1" }]);
  assert.deepEqual(names(sorted), ["E1", "E2", "E10"]);
}

// 3. 中文集数（第N集）按数值排序。
{
  const sorted = sortItemsByName([{ name: "第10集.mkv" }, { name: "第2集.mkv" }, { name: "第1集.mkv" }]);
  assert.deepEqual(names(sorted), ["第1集.mkv", "第2集.mkv", "第10集.mkv"]);
}

// 4. 完整文件名：同前缀剧集按季集数值排，而不是字典序。
{
  const files = [
    { name: "初来乍到 - S03E23 - 第23集.mkv" },
    { name: "初来乍到 - S03E03 - 第3集.mkv" },
    { name: "初来乍到 - S03E01 - 第1集.mkv" },
    { name: "初来乍到 - S03E10 - 第10集.mkv" }
  ];
  const sorted = sortItemsByName([...files]);
  assert.deepEqual(names(sorted), [
    "初来乍到 - S03E01 - 第1集.mkv",
    "初来乍到 - S03E03 - 第3集.mkv",
    "初来乍到 - S03E10 - 第10集.mkv",
    "初来乍到 - S03E23 - 第23集.mkv"
  ]);
}

// 5. 大小写不敏感比较（sensitivity: base），同名不同大小写保持原相对顺序（稳定排序）。
{
  const sorted = sortItemsByName([{ name: "B.mkv" }, { name: "a.mkv" }, { name: "A.mkv" }]);
  assert.deepEqual(names(sorted), ["a.mkv", "A.mkv", "B.mkv"]);
}

// 6. 空数组与缺名字段不抛错（缺 name 视为空串参与排序，条目原样保留）。
{
  assert.deepEqual(names(sortItemsByName([])), []);
  const sorted = sortItemsByName([{ id: 2 }, { id: 1, name: "x" }]);
  assert.deepEqual(names(sorted), [undefined, "x"]);
}

console.log("rename-selection-order: 6 组断言全部通过");
