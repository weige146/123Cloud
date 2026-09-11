// 操作记录回归测试：整理/批量重命名按刮削文件夹名落库（同名合并）、
// 还原前的行状态分类、还原计划（原目录分组 / 锚点重建 / 改回名）、锚点解析。
// 用法：node 油猴脚本/123-helper/tests/operation-records.test.mjs
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
  slice("// src/core/categories.js", "// src/icons.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");
const driver = `;
globalThis.__records = {
  operationRecordNameForTask,
  buildOrganizeRecordGroups,
  buildRenameRecordGroups,
  mergeRecordsIntoStore,
  classifyRecordRows,
  planRecordRestore,
  resolveRecordAnchors,
  sortedRecordNames
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException, structuredClone };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const {
  operationRecordNameForTask,
  buildOrganizeRecordGroups,
  buildRenameRecordGroups,
  mergeRecordsIntoStore,
  classifyRecordRows,
  planRecordRestore,
  resolveRecordAnchors,
  sortedRecordNames
} = sandbox.__records;

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    try {
      await fn();
      passed += 1;
      console.log(`  \u001b[32m✓\u001b[0m ${title}`);
    } catch (error) {
      console.error(`  \u001b[31m✗\u001b[0m ${title}`);
      throw error;
    }
  });
};

// vm 沙箱产物与主沙箱原型不同，deepEqual 前先转成普通对象
const plain = (value) => JSON.parse(JSON.stringify(value));

test("记录名：mediaFolder 优先，退回目标路径非 Season 最深一层，再退来源目录名", () => {
  assert.equal(operationRecordNameForTask({ fields: { mediaFolder: "庆余年 (2024)" } }), "庆余年 (2024)");
  assert.equal(operationRecordNameForTask({ fields: {}, folderParts: ["电视剧", "庆余年 (2024)", "Season 2"] }), "庆余年 (2024)");
  assert.equal(operationRecordNameForTask({ fields: {}, folderParts: [], sourceFolderName: "第二季素材" }), "第二季素材");
  assert.equal(operationRecordNameForTask({ fields: {}, folderParts: ["Season 1"] }), "未命名记录");
});

test("buildOrganizeRecordGroups：按刮削文件夹名拆分，失败行剔除，回收站行保留，详情随行带出", () => {
  const snapshot = {
    currentDir: "root1",
    tasks: [
      { id: "f1", name: "a.mkv", fields: { mediaFolder: "庆余年 (2024)", seasonEpisode: "S01E01", mediaType: "tv" }, sourceTitle: "庆余年" },
      { id: "f2", name: "b.mkv", fields: { mediaFolder: "庆余年 (2024)" }, sourceTitle: "庆余年" },
      { id: "f3", name: "c.mp4", fields: { mediaFolder: "星战4 (1977)", mediaType: "movie" }, sourceTitle: "星战4" },
      { id: "f4", name: "d.mkv", fields: { mediaFolder: "庆余年 (2024)" } }
    ]
  };
  const rows = [
    { id: "f1", parentId: "p1", name: "a.mkv", newName: "庆余年 S01E01.mkv", targetDir: "t1", targetPath: "庆余年 (2024)/Season 1/庆余年 S01E01.mkv", discarded: false, failed: false, type: 0, size: 100 },
    { id: "f2", parentId: "p1", name: "b.mkv", newName: "庆余年 S01E02.mkv", targetDir: "t1", targetPath: "x", discarded: true, failed: false, type: 0, size: 2 },
    { id: "f3", parentId: "p2", name: "c.mp4", newName: "星战4 (1977).mp4", targetDir: "t2", targetPath: "y", discarded: false, failed: false, type: 0, size: 3 },
    { id: "f4", parentId: "p1", name: "d.mkv", newName: "d.mkv", targetDir: "", targetPath: "", discarded: false, failed: true, type: 0, size: 4 }
  ];
  const groups = buildOrganizeRecordGroups(snapshot, rows, { p1: { homeId: "root1", chain: ["素材"] }, p2: { homeId: "root1", chain: ["电影"] } });
  assert.deepEqual(plain(groups.map((group) => group.name).sort()), ["庆余年 (2024)", "星战4 (1977)"]);
  const qyn = groups.find((group) => group.name === "庆余年 (2024)");
  assert.equal(qyn.rows.length, 2);
  const row1 = qyn.rows.find((row) => row.id === "f1");
  assert.equal(row1.info.seasonEpisode, "S01E01");
  assert.equal(row1.info.groupTitle, "庆余年");
  assert.equal(row1.homeId, "root1");
  assert.deepEqual(plain(row1.homeChain), ["素材"]);
  assert.equal(qyn.rows.find((row) => row.id === "f2").discarded, true);
  assert.equal(groups.find((group) => group.name === "星战4 (1977)").rows.length, 1);
});

test("mergeRecordsIntoStore：同名合并按文件 id 覆盖旧行，行按文件名自然排序", () => {
  const store = { version: 1, records: {} };
  mergeRecordsIntoStore(store, [{ name: "剧 A", kind: "organize", rows: [{ id: "1", name: "b.mkv", newName: "B.mkv", parentId: "p" }, { id: "2", name: "a.mkv", newName: "A.mkv", parentId: "p" }] }], { modeLabel: "原地整理" });
  mergeRecordsIntoStore(store, [{ name: "剧 A", kind: "organize", rows: [{ id: "2", name: "a.mkv", newName: "A2.mkv", parentId: "p" }, { id: "3", name: "c.mkv", newName: "C.mkv", parentId: "p" }] }], {});
  const record = store.records["剧 A"];
  // 行按文件名自然排序：a.mkv(2) → b.mkv(1) → c.mkv(3)
  assert.deepEqual(plain(record.rows.map((row) => row.id)), ["2", "1", "3"]);
  // 文件 2 的新行覆盖旧行
  assert.equal(record.rows.find((row) => row.id === "2").newName, "A2.mkv");
  assert.equal(record.modeLabel, "原地整理");
  // 不同名 → 新记录
  mergeRecordsIntoStore(store, [{ name: "剧 B", kind: "organize", rows: [{ id: "9", name: "x.mkv", newName: "X.mkv", parentId: "p" }] }], {});
  assert.deepEqual(sortedRecordNames(store), ["剧 A", "剧 B"]);
});

test("mergeRecordsIntoStore：记录数超上限淘汰最旧，单记录行数超限截断并标注", () => {
  const store = { version: 1, records: {} };
  for (let i = 0; i < 50; i += 1) {
    store.records[`旧记录 ${String(i).padStart(2, "0")}`] = { name: `旧记录 ${String(i).padStart(2, "0")}`, kind: "organize", createdAt: "2026-01-01", updatedAt: `2026-01-${String(i + 1).padStart(2, "0")}`, rows: [{ id: `old-${i}`, name: "o", newName: "n", parentId: "p" }] };
  }
  mergeRecordsIntoStore(store, [{ name: "新记录", kind: "organize", rows: [{ id: "n1", name: "n.mkv", newName: "N.mkv", parentId: "p" }] }], {});
  assert.equal(Object.keys(store.records).length, 50);
  assert.equal(store.records["旧记录 00"], undefined);
  assert.ok(store.records["新记录"]);

  const fat = { version: 1, records: {} };
  const manyRows = Array.from({ length: 3005 }, (_, index) => ({ id: String(index), name: `${String(index).padStart(5, "0")}.mkv`, newName: "n", parentId: "p" }));
  mergeRecordsIntoStore(fat, [{ name: "大记录", kind: "organize", rows: manyRows }], {});
  assert.equal(fat.records["大记录"].rows.length, 3000);
  assert.equal(fat.records["大记录"].truncated, true);
});

test("classifyRecordRows：restore / already / changed / missing / discarded 五态", () => {
  const rows = [
    { id: "1", name: "a.mkv", newName: "A.mkv", parentId: "p1", targetDir: "t1", discarded: false },
    { id: "2", name: "b.mkv", newName: "B.mkv", parentId: "p1", targetDir: "t1", discarded: false },
    { id: "3", name: "c.mkv", newName: "C.mkv", parentId: "p1", targetDir: "t1", discarded: false },
    { id: "4", name: "d.mkv", newName: "D.mkv", parentId: "p1", targetDir: "t1", discarded: false },
    { id: "5", name: "e.mkv", newName: "E.mkv", parentId: "p1", targetDir: "t1", discarded: true },
    { id: "6", name: "f.mkv", newName: "F.mkv", parentId: "p1", targetDir: "", discarded: false },
    { id: "7", name: "g.nfo", newName: "g.nfo", parentId: "p1", targetDir: "", discarded: true },
    { id: "8", name: "h.mkv", newName: "H.mkv", parentId: "p1", targetDir: "t1", discarded: false }
  ];
  const live = new Map([
    ["1", { name: "A.mkv", parentId: "t1" }],
    ["2", { name: "b.mkv", parentId: "p1" }],
    ["3", { name: "c.mkv", parentId: "t9" }],
    ["4", { name: "改过名.mkv", parentId: "t1" }],
    ["7", { name: "g.nfo", parentId: "p1" }],
    ["8", { name: "H.mkv", parentId: "t1", TrashedAt: "2026-09-12" }]
  ]);
  const result = Object.fromEntries(classifyRecordRows(rows, live).map((item) => [item.row.id, item.status]));
  assert.equal(result["1"], "restore");
  assert.equal(result["2"], "already");
  // 名字已改回但还留在别处 → 只需移回，也算 restore
  assert.equal(result["3"], "restore");
  // 名字对不上 → 被再次动过
  assert.equal(result["4"], "changed");
  assert.equal(result["5"], "discarded");
  // 重命名记录没有 targetDir：名字等于新名即算 restore
  assert.equal(result["6"], "missing");
  // 回收站行已被手动还原回原位 → 直接算已还原
  assert.equal(result["7"], "already");
  // 普通行但文件已被再次清进回收站（带 TrashedAt 标记）→ 查不到，不自动还原
  assert.equal(result["8"], "missing");
});

test("planRecordRestore：改回名用实时父目录，移回按原父目录分组，原目录没了走锚点重建，锚点也没有则报不可还原", () => {
  const rows = [
    { id: "1", name: "a.mkv", newName: "A.mkv", parentId: "p1", homeId: "home", homeChain: ["素材"], targetDir: "t1" },
    { id: "2", name: "b.mkv", newName: "B.mkv", parentId: "p1", homeId: "home", homeChain: ["素材"], targetDir: "t1" },
    { id: "3", name: "c.mkv", newName: "C.mkv", parentId: "dead", homeId: "home", homeChain: ["素材", "子目录"], targetDir: "t1" },
    { id: "4", name: "d.mkv", newName: "D.mkv", parentId: "dead2", homeId: "dead2", homeChain: null, targetDir: "t1" },
    { id: "5", name: "e.mkv", newName: "E.mkv", parentId: "p2", homeId: "p2", homeChain: [], targetDir: "t1" }
  ];
  const live = new Map([
    ["1", { name: "A.mkv", parentId: "t1" }],
    ["2", { name: "b.mkv", parentId: "t1" }],
    ["3", { name: "C.mkv", parentId: "t1" }],
    ["4", { name: "D.mkv", parentId: "t1" }],
    ["5", { name: "E.mkv", parentId: "p2" }]
  ]);
  const alive = new Set(["p1", "p2", "home"]);
  const plan = planRecordRestore(rows, live, alive);
  // 行 1 当前叫 A.mkv 要改回 a.mkv（在当前位置 t1 改，配合互换保护）
  assert.ok(plan.renames.some((item) => item.id === "1" && item.name === "A.mkv" && item.newName === "a.mkv" && item.parentId === "t1"));
  // 行 2 当前名就是原名 b.mkv → 只移回、不改名
  assert.equal(plan.renames.some((item) => item.id === "2"), false);
  // 移回按原父目录分组：行 1、2 都回 p1
  assert.deepEqual(plain(plan.moves.get("p1")), ["1", "2"]);
  // 原父目录 dead 不在存活集 → 走锚点重建
  assert.equal(plan.recreates.length, 1);
  assert.equal(plan.recreates[0].homeId, "home");
  assert.deepEqual(plain(plan.recreates[0].chain), ["素材", "子目录"]);
  assert.deepEqual(plain(plan.recreates[0].ids), ["3"]);
  // 原目录与锚点都没了
  assert.equal(plan.unrestorable.length, 1);
  assert.equal(plan.unrestorable[0].row.id, "4");
  // 行 5 已在原父目录 p2：不移回，但当前名 E.mkv ≠ 原名 e.mkv，仍需改回名
  assert.equal(plan.moves.get("p2"), undefined);
  assert.ok(plan.renames.some((item) => item.id === "5" && item.newName === "e.mkv"));
});

test("resolveRecordAnchors：源父目录向上走到扫描根记下目录名链，扫描根外记 null", async () => {
  const infos = {
    p1: { name: "B 目录", parentId: "mid" },
    mid: { name: "A 目录", parentId: "root" },
    root: { name: "媒体库", parentId: "0" },
    outside: { name: "别处", parentId: "other" },
    other: { name: "更上层", parentId: "0" }
  };
  const api = { fileInfos: async (ids) => ids.map((id) => (infos[String(id)] ? { id: Number(id), name: infos[String(id)].name, parentId: infos[String(id)].parentId } : null)).filter(Boolean) };
  const snapshot = { currentDir: "root" };
  const rows = [
    { id: "1", parentId: "p1", discarded: false },
    { id: "2", parentId: "root", discarded: false },
    { id: "3", parentId: "outside", discarded: false }
  ];
  const { anchors, scopeName } = await resolveRecordAnchors(api, snapshot, rows);
  assert.equal(scopeName, "媒体库");
  assert.deepEqual(plain(anchors.p1), { homeId: "root", chain: ["A 目录", "B 目录"] });
  assert.deepEqual(plain(anchors.root), { homeId: "root", chain: [] });
  assert.equal(anchors.outside.homeId, "");
  assert.equal(anchors.outside.chain, null);
});

test("buildRenameRecordGroups：按所在目录名分组，取不到目录名退回目录 id 标签", () => {
  const groups = buildRenameRecordGroups([
    { id: "1", parentId: "p1", name: "a.txt", newName: "A.txt", type: 0, size: 1 },
    { id: "2", parentId: "p1", name: "b.txt", newName: "B.txt", type: 0, size: 2 },
    { id: "3", parentId: "p2", name: "c.txt", newName: "C.txt" }
  ], { p1: "文档目录" });
  assert.deepEqual(plain(groups.map((group) => group.name)), ["文档目录", "目录 p2"]);
  assert.equal(groups[0].rows.length, 2);
  assert.equal(groups[0].kind, "rename");
});

await chain;
console.log(`\n操作记录测试通过：${passed} 项`);
