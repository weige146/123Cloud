// 整理执行阶段同名复查回归测试：预检基于预览快照，首整时目标目录还不存在、
// 预览与执行之间目录也可能变动，漏网同名在平台 duplicate:1 语义下会静默变成
// 「名字(1)」副本。执行阶段在目录建好后按目录实时核对一次，后到者移入回收站。
// 用法：node 油猴脚本/123-helper/tests/organize-exec-recheck.test.mjs
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
  slice("// src/core/organize.js", "// src/core/organize-strategy.js")
].join("\n");
const driver = `;
globalThis.__recheck = { executeOrganizePreview, batchResult };
`;
const sandbox = { console, setTimeout, clearTimeout, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakMap, RegExp, Intl, Symbol, Error, Promise, DOMException, crypto };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { executeOrganizePreview, batchResult } = sandbox.__recheck;

// —— 假 API：listAll 按 目录ID → 条目 返回，其余方法只记录调用 ——
function createMockApi({ filesByDir = new Map(), listAllError = null, dirIdByParts } = {}) {
  const calls = { listAll: [], trash: [], move: [], renameMany: [] };
  const api = {
    writeConcurrency: 3,
    async listAll(dirId) {
      calls.listAll.push(String(dirId));
      if (listAllError) throw listAllError;
      return filesByDir.get(String(dirId)) || [];
    },
    async trash(filesArg) {
      const files = Array.from(filesArg || []);
      calls.trash.push(files.map((file) => String(file.id)));
    },
    async rename(fileId, fileName) {
      return {};
    },
    async renameMany(targetsArg) {
      const targets = Array.from(targetsArg || []);
      calls.renameMany.push(targets.map((target) => String(target.id)));
      const details = targets.map((target) => ({ ...target, status: "success" }));
      return batchResult(details, []);
    },
    async ensurePath(rootId, parts) {
      const key = parts.join("/");
      return String(dirIdByParts ? dirIdByParts(key) : "dir-100");
    },
    async move(fileIdsArg, parentFileId) {
      const fileIds = Array.from(fileIdsArg || []);
      calls.move.push({ ids: fileIds.map((id) => String(id)), to: String(parentFileId) });
    },
    async fileInfos(ids) {
      return [];
    }
  };
  return { api, calls };
}

function buildSnapshot(tasks) {
  return {
    snapshotVersion: 1,
    createdAt: "2026-09-17T00:00:00.000Z",
    mode: { location: "library", naming: "normalized" },
    currentDir: "0",
    tasks: tasks.map((task) => ({ ...task, fields: { ...(task.fields || {}) }, folderParts: [...(task.folderParts || [])] })),
    groups: [],
    sourceFolders: [],
    warnings: [],
    summary: { files: tasks.length, renames: 0, discards: 0, conflicts: 0, moves: 0 }
  };
}

const config = { library: { rootId: "root-1" }, requests: { moveInterval: 0, moveBatchSize: 100, writeConcurrency: 3 } };
const task = (id, newName, extra = {}) => ({ id, name: `原始名 ${id}.mkv`, newName, parentId: "src-1", folderParts: ["剧名 (2024)", "Season 1"], discard: false, ...extra });

let passed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ok ${name}`);
  } catch (error) {
    console.error(`  FAIL ${name}`);
    throw error;
  }
}

await test("执行复查：目标目录已存在同名文件时，后到文件跳过并移入回收站，不再移动", async () => {
  const filesByDir = new Map([["dir-100", [{ id: 901, name: "剧名 S01E01.mkv", type: 0 }]]]);
  const { api, calls } = createMockApi({ filesByDir });
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv"),
    task(102, "剧名 S01E02.mkv")
  ]);
  const result = await executeOrganizePreview(api, snapshot, config, {});
  const detailF1 = result.details.find((row) => row.id === 101);
  const detailF2 = result.details.find((row) => row.id === 102);
  const rowF1 = result.rows.find((row) => row.id === 101);
  assert.equal(detailF1.status, "skipped", "f1 跳过");
  assert.ok(detailF1.message.includes("执行时复查"), `f1 文案应指明执行时复查：${detailF1.message}`);
  assert.ok(rowF1.discarded, "f1 标记已回收");
  assert.equal(detailF2.status, "success", "f2 无冲突正常完成");
  assert.deepEqual(calls.trash, [["101"]], "只有 f1 进回收站");
  assert.deepEqual(calls.move.flatMap((call) => call.ids), ["102"], "只移动 f2");
  assert.equal(result.discardedFiles, 1);
});

await test("执行复查成本：两个任务同一目标目录只列举一次", async () => {
  const { api, calls } = createMockApi({});
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv"),
    task(102, "剧名 S01E02.mkv")
  ]);
  await executeOrganizePreview(api, snapshot, config, {});
  assert.deepEqual(calls.listAll, ["dir-100"], "每个目标目录恰好一次列举");
  assert.deepEqual(calls.trash, [], "无冲突不动回收站");
  assert.deepEqual(calls.move.flatMap((call) => call.ids).sort(), ["101", "102"]);
});

await test("执行复查：任务内同名漏网（预览后手改撞名）兜底进回收站", async () => {
  const { api, calls } = createMockApi({});
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv"),
    task(102, "剧名 S01E01.mkv")
  ]);
  const result = await executeOrganizePreview(api, snapshot, config, {});
  const detailF1 = result.details.find((row) => row.id === 101);
  const detailF2 = result.details.find((row) => row.id === 102);
  const rowF2 = result.rows.find((row) => row.id === 102);
  assert.equal(detailF1.status, "success", "先到者保留");
  assert.equal(detailF2.status, "skipped", "后到者跳过");
  assert.ok(rowF2.discarded, "后到者已回收");
  assert.ok(detailF2.message.includes("本次任务中已有同名目标"), `后到者文案：${detailF2.message}`);
  assert.deepEqual(calls.trash, [["102"]]);
  assert.deepEqual(calls.move.flatMap((call) => call.ids), ["101"]);
});

await test("执行复查：目录列举失败时退回预检结论，不阻塞整理", async () => {
  const { api, calls } = createMockApi({ listAllError: new Error("网络错误") });
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv"),
    task(102, "剧名 S01E02.mkv")
  ]);
  const result = await executeOrganizePreview(api, snapshot, config, {});
  assert.deepEqual(calls.trash, [], "列举失败不回收");
  assert.deepEqual(calls.move.flatMap((call) => call.ids).sort(), ["101", "102"], "照常移动");
  assert.deepEqual(result.details.filter((row) => row.status === "success").length, 2);
});

await test("执行复查：已存在同名文件是任务文件本身时不误判", async () => {
  // unchangedLocation 场景：文件已在目标目录里（id 与目录内条目相同）
  const filesByDir = new Map([["dir-100", [{ id: 101, name: "剧名 S01E01.mkv", type: 0 }, { id: 102, name: "剧名 S01E02.mkv", type: 0 }]]]);
  const { api, calls } = createMockApi({ filesByDir });
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv", { parentId: "dir-100" }),
    task(102, "剧名 S01E02.mkv", { parentId: "dir-100" })
  ]);
  const result = await executeOrganizePreview(api, snapshot, config, {});
  assert.deepEqual(calls.trash, [], "自己撞自己不回收");
  assert.deepEqual(calls.move, [], "原地任务不再移动");
  assert.equal(result.details.filter((row) => row.status === "success").length, 2);
});

await test("执行复查：不同目标目录分别核对，同名互不干扰", async () => {
  const filesByDir = new Map([["dir-200", [{ id: 902, name: "剧名 S02E01.mkv", type: 0 }]]]);
  const { api, calls } = createMockApi({ filesByDir, dirIdByParts: (key) => (key.includes("Season 2") ? "dir-200" : "dir-100") });
  const snapshot = buildSnapshot([
    task(101, "剧名 S01E01.mkv", { folderParts: ["剧名 (2024)", "Season 1"] }),
    task(102, "剧名 S02E01.mkv", { folderParts: ["剧名 (2024)", "Season 2"] })
  ]);
  const result = await executeOrganizePreview(api, snapshot, config, {});
  assert.deepEqual([...calls.listAll].sort(), ["dir-100", "dir-200"], "两个目录各核对一次");
  const detailF1 = result.details.find((row) => row.id === 101);
  const detailF2 = result.details.find((row) => row.id === 102);
  assert.equal(detailF1.status, "success", "Season 1 无冲突");
  assert.equal(detailF2.status, "skipped", "Season 2 里的同名后到者跳过");
  assert.ok(detailF2.message.includes("执行时复查"));
  assert.equal(result.discardedFiles, 1);
});

console.log(`\n organize-exec-recheck: ${passed} 个用例全部通过`);
