// 秒传任务流程回归：导出「扫完一个顶层项就交付一个」（onArtifact 逐顶层项回调）与任务队列簿记，
// 以及点击即开跑的接线（开跑前不探测线路、队列在进度条与右下角小窗都可见、动作都有处理器）。
// 用法：node 123-helper/tests/fastlink-task-flow.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const lines = fs.readFileSync(scriptPath, "utf8").split("\n");
const bundle = lines.join("\n");
const slice = (fromMarker, toMarker) => {
  const start = lines.findIndex((line) => line.includes(fromMarker));
  const end = lines.findIndex((line) => line.includes(toMarker));
  if (start < 0 || end <= start) throw new Error(`bundle markers not found: ${fromMarker} .. ${toMarker}`);
  return lines.slice(start, end).join("\n");
};
const code = [
  slice("// src/core/utils.js", "// src/api.js"),
  slice("// src/api.js", "// src/core/categories.js"),
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__flow = { exportFastlinkItems, createFastlinkTaskQueue, createRunLedger };
`;
const storageMap = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, setTimeout, clearTimeout, AbortController,
  location: { origin: "https://www.123pan.cn" },
  localStorage: {
    getItem: (key) => (storageMap.has(key) ? storageMap.get(key) : null),
    setItem: (key, value) => storageMap.set(key, String(value)),
    removeItem: (key) => storageMap.delete(key)
  }
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { exportFastlinkItems, createFastlinkTaskQueue, createRunLedger } = sandbox.__flow;

const etag = (n) => String(n).padStart(2, "0").repeat(16);
// 三个顶层文件夹，各自下面挂一个子目录（共 6 次列目录请求），用来验证交付时机
function makeApi() {
  const folders = new Map([
    ["a1", [{ id: "a1s", name: "sub", type: 1 }]],
    ["a1s", [{ id: "a1f", name: "Movie.A.mkv", type: 0, size: 10, etag: etag(1) }]],
    ["b1", [{ id: "b1s", name: "sub", type: 1 }]],
    ["b1s", [{ id: "b1f", name: "Movie.B.mkv", type: 0, size: 20, etag: etag(2) }]],
    ["c1", [{ id: "c1s", name: "sub", type: 1 }]],
    ["c1s", [{ id: "c1f", name: "Movie.C.mkv", type: 0, size: 30, etag: etag(3) }]]
  ]);
  const api = { listed: [], fileInfosCalls: 0 };
  api.listAll = async (id) => {
    await new Promise((resolve) => setTimeout(resolve, 1));
    api.listed.push(String(id));
    return folders.get(String(id)) || [];
  };
  api.fileInfos = async () => {
    api.fileInfosCalls += 1;
    return [];
  };
  return api;
}
const items = () => [
  { id: "a1", name: "A", type: 1 },
  { id: "b1", name: "B", type: 1 },
  { id: "c1", name: "C", type: 1 }
];

// —— 用例 1：每扫完一个顶层项就交付一个，交付时后面的项还没开始列目录 ——
{
  const api = makeApi();
  const delivered = [];
  const listedAtDelivery = [];
  const artifacts = await exportFastlinkItems(api, items(), {
    useFolderName: true,
    onArtifact: (artifact, done, total) => {
      delivered.push({ name: artifact.item.name, files: artifact.fileCount, done, total, link: artifact.link });
      listedAtDelivery.push(api.listed.length);
    }
  });
  assert.equal(delivered.map((entry) => entry.name).join(","), "A,B,C", "交付顺序与顶层项顺序一致");
  assert.equal(delivered.map((entry) => entry.done).join(","), "1,2,3", "回调带上了第几项");
  assert.equal(delivered.map((entry) => entry.total).join(","), "3,3,3");
  assert.equal(delivered.map((entry) => entry.files).join(","), "1,1,1");
  assert.ok(listedAtDelivery[0] <= 2, `第一项交付时不该把后面的目录也扫完（实际已列 ${listedAtDelivery[0]} 个目录）`);
  assert.ok(listedAtDelivery[1] <= 4, `第二项交付时最多列到第四本目录（实际 ${listedAtDelivery[1]}）`);
  assert.equal(artifacts.length, 3, "返回值仍是完整产物列表");
  assert.equal(artifacts.map((artifact) => artifact.item.name).join(","), "A,B,C", "返回顺序一致");
  assert.ok(delivered[0].link.startsWith("123FLCPV2$"), "产物带得上秒传链接文本");
  console.log(`ok 逐顶层项交付（第一项交付时只列了 ${listedAtDelivery[0]} 个目录）`);
}

// —— 用例 2：不传 onArtifact 时行为不变（既有调用方零改动）——
{
  const api = makeApi();
  const artifacts = await exportFastlinkItems(api, items(), { useFolderName: true });
  assert.equal(artifacts.length, 3);
  assert.ok(artifacts.every((artifact) => artifact.fileCount === 1));
  console.log("ok 不传 onArtifact 时导出结果不变");
}

// —— 用例 3：中途某项扫描失败，前面已交付的产物保持交付（调用方靠断点做抢救导出）——
{
  const api = makeApi();
  const original = api.listAll.bind(api);
  api.listAll = async (id) => {
    if (String(id) === "c1") throw new Error("目录读取失败");
    return original(id);
  };
  const delivered = [];
  await assert.rejects(() => exportFastlinkItems(api, items(), { onArtifact: (artifact) => delivered.push(artifact.item.name) }), /目录读取失败/);
  assert.equal(delivered.join(","), "A,B", "失败前已扫完的项应已经交付");
  console.log("ok 扫描中断时已交付的产物不回收");
}

// —— 用例 4：队列簿记——先进先出、可单独取消、可清空 ——
{
  const queue = createFastlinkTaskQueue();
  assert.equal(queue.length, 0);
  assert.equal(queue.next(), null, "空队列不产出任务");
  const first = queue.add("生成秒传", () => "first");
  const second = queue.add("导入秒传", () => "second");
  const third = queue.add("", () => "third");
  assert.equal(queue.length, 3);
  assert.equal(queue.snapshot().map((entry) => entry.label).join(","), "生成秒传,导入秒传,秒传任务", "空标签回落为「秒传任务」");
  assert.equal(queue.remove(second.id).label, "导入秒传");
  assert.equal(queue.remove("不存在的id"), null);
  assert.equal(queue.snapshot().map((entry) => entry.label).join(","), "生成秒传,秒传任务", "取消后保持先进先出");
  assert.equal(queue.next().label, "生成秒传");
  assert.equal(queue.next().label, "秒传任务");
  queue.add("再来一个", () => null);
  queue.add("再再来一个", () => null);
  assert.equal(queue.clear().length, 2);
  assert.equal(queue.length, 0);
  assert.notEqual(first.id, third.id, "每条意图有独立 id");
  console.log("ok 秒传任务队列先进先出、可单独取消与清空");
}

// —— 用例 6：明细账本不开调试模式也逐条记文件（明细默认展开，得真有内容可看）——
{
  const ledger = createRunLedger({ limit: 200, verboseLimit: 800 });
  ledger.detail({ kind: "file", path: "剧/E01.1080p-GROUP1.mkv", size: 1024, files: 1, folders: 1, bytes: 1024 });
  ledger.detail({ kind: "file", path: "剧/E02.1080p-GROUP2.mkv", size: 2048, files: 2, folders: 1, bytes: 3072 });
  const lines = ledger.lines.map((line) => line.text).join("\n");
  assert.ok(lines.includes("E01.1080p-GROUP1.mkv"), "紧凑模式也要记下扫过的文件");
  assert.ok(lines.includes("E02.1080p-GROUP2.mkv"));
  assert.equal(ledger.counters.files, 2);
  for (let index = 0; index < 3000; index += 1) {
    ledger.detail({ kind: "file", path: `\u5267/file-${index}.mkv`, size: 10, files: index + 3, folders: 1, bytes: 10 });
  }
  assert.ok(ledger.lines.length <= 200, `环形缓冲要封顶（实际 ${ledger.lines.length} 行）`);
  assert.ok(ledger.lines.at(-1).text.includes("file-2999"), "保留的是最近的明细");
  assert.ok(String(ledger.stats().currentFile).includes("file-2999"), "账本始终记着当前条目");
  assert.ok(/\u6587\u4ef6 3002/.test(ledger.summary("")), "摘要带上累计处理计数");
  console.log(`ok 明细逐条记文件并封顶 ${ledger.lines.length} 行`);
}

// —— 用例 5：控制器接线源码契约（防后续改动把「立即开跑 / 逐顶层项交付 / 队列」拆断）——
{
  const runnerFrom = bundle.indexOf("async runFastlinkTask(kind, worker) {");
  const runnerTo = bundle.indexOf("\u5404\u79D2\u4F20\u6D41\u7A0B\u7EDF\u4E00\u7528\u8FD9\u4E2A\u660E\u7EC6\u56DE\u8C03");
  const runner = bundle.slice(runnerFrom, runnerTo);
  assert.ok(runnerFrom > 0 && runnerTo > runnerFrom, "应能定位到秒传任务执行体");
  assert.ok(runner.includes("startFastlinkTask(kind)"), "定位到的应是任务执行体");
  assert.ok(!runner.includes("probeDriveRoute("), "任务开始时不得再自动发探测请求");
  assert.ok(runner.includes("beginDriveRoute(this.api"), "任务开始仍应用线路（默认页面同源）");
  assert.equal((bundle.match(/this\.queueFastlinkTask\(/g) || []).length, 5, "五个秒传入口都要接队列");
  assert.ok(bundle.includes('onArtifact: async (artifact, done) =>'), "导出要逐顶层项交付产物");
  assert.ok(bundle.includes("progressMinimized: false, progressLedgerOpen: true"), "进度明细默认展开");
  assert.equal((bundle.match(/\$\{renderFastlinkQueue\(ui\)\}/g) || []).length, 3, "队列在进度条与两个小窗状态都渲染");
  assert.ok(bundle.includes('"fastlink-queue-remove": (control) => this.removeQueuedFastlinkTask(control.dataset.id)'), "单条取消要有动作处理器");
  assert.ok(bundle.includes('"fastlink-queue-clear": () => this.clearQueuedFastlinkTasks()'), "全部取消要有动作处理器");
  assert.ok(bundle.includes('"fastlink-queue-remove", "stop"]'), "任务运行中也要放行队列取消按钮");
  assert.ok(bundle.includes('data-action="fastlink-route-probe"'), "调试模式要保留手动测速入口");
  console.log("ok 立即开跑、逐顶层项交付与队列的接线完整");
}

console.log("fastlink-task-flow 全部通过");
