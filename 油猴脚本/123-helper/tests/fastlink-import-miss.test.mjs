// 千万级流式导入的分桶与断点语义回归：秒传未命中不算失败、有未命中时绝不清断点
// （清了下一次会把已成功条目再转一遍、duplicate:1 下生成"名字 (1)"副本）、
// 限速门水位随断点落盘、明细事件契约。
// 用法：node 油猴脚本/123-helper/tests/fastlink-import-miss.test.mjs
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
  slice("// src/core/fastlink.js", "// src/public-share-cleanup.js")
].join("\n");
const driver = `;
globalThis.__mod = { createFastlinkStreamImporter, readFastlinkStreamCheckpoint, clearFastlinkStreamCheckpoint, panApiGates };
`;
const storageMap = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://www.123865.com" },
  localStorage: {
    getItem: (key) => (storageMap.has(key) ? storageMap.get(key) : null),
    setItem: (key, value) => storageMap.set(key, String(value)),
    removeItem: (key) => storageMap.delete(key)
  },
  setTimeout, clearTimeout, AbortController,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { createFastlinkStreamImporter, readFastlinkStreamCheckpoint, clearFastlinkStreamCheckpoint, panApiGates } = sandbox.__mod;

const MISS_ETAG = "ff".repeat(16);
const entry = (name, etag) => ({ path: name, etag, size: 1024, fileName: name });
function makeApi() {
  const calls = [];
  return {
    calls,
    laneHost: "",
    async ensurePath() {
      return "0";
    },
    async reuseFile(file, parentId) {
      calls.push(file.path);
      if (file.etag === MISS_ETAG) {
        const error = new Error("\u4E91\u7AEF\u6CA1\u6709\u53EF\u590D\u7528\u7684\u540C\u54C8\u5E0C\u6587\u4EF6");
        error.fastlinkMiss = true;
        throw error;
      }
      return "1";
    }
  };
}

// —— 用例 1：全部秒传成功 → 清断点、无失败无未命中 ——
{
  clearFastlinkStreamCheckpoint();
  const api = makeApi();
  const details = [];
  const importer = createFastlinkStreamImporter(api, "0", { onDetail: (event) => details.push(event) });
  await importer.consume([entry("a.mkv", "01".repeat(16)), entry("b.mkv", "02".repeat(16))]);
  const result = await importer.finish();
  assert.equal(result.ok, 2, "两条都应秒传成功");
  assert.equal(result.fail, 0);
  assert.equal(result.miss, 0, "全部命中时不该有未命中");
  assert.equal(result.bytes, 2048, "应累计已转存体积");
  assert.equal(readFastlinkStreamCheckpoint(), null, "全部成功应清断点（重跑幂等）");
  assert.ok(details.every((event) => event.kind === "import" && event.status === "success"), "明细事件应带成功状态");
  console.log("ok 全部秒传成功：清断点、体积与明细计数正确");
}

// —— 用例 2：部分未命中 → 单列一桶、不算失败、不清断点、重跑只补未命中 ——
{
  clearFastlinkStreamCheckpoint();
  const api = makeApi();
  const batch = [entry("ok1.mkv", "0a".repeat(16)), entry("nope.mkv", MISS_ETAG), entry("ok2.mkv", "0b".repeat(16))];
  const first = createFastlinkStreamImporter(api, "0", {});
  await first.consume(batch);
  const result = await first.finish();
  assert.equal(result.ok, 2);
  assert.equal(result.miss, 1, "未命中应单列一桶");
  assert.equal(result.fail, 0, "未命中不该算失败");
  assert.equal(result.status, "success", "只有未命中时任务整体不算部分失败");
  const record = readFastlinkStreamCheckpoint();
  assert.ok(record, "有未命中时必须保留断点（否则重跑会把已成功条目再转一遍）");
  assert.equal(record.rootId, "0");
  assert.equal(record.doneCount, 2, "断点只记录真正成功的条目");
  assert.ok(record.pacing && record.pacing.write && record.pacing.list, "限速门水位应随断点落盘");
  // 重跑同一批：已成功两条被 doneSet 跳过，只再试未命中那条
  const api2 = makeApi();
  const again = createFastlinkStreamImporter(api2, "0", {});
  await again.consume(batch);
  const second = await again.finish();
  assert.equal(second.skipped, 2, `已成功条目应按断点跳过（实际 ${second.skipped}）`);
  assert.deepEqual(api2.calls, ["nope.mkv"], "重跑只应补未命中那条");
  clearFastlinkStreamCheckpoint();
  console.log("ok 未命中单列一桶、保留断点，重跑只补未命中");
}

// —— 用例 3：真失败与未命中分列，明细样本各自封顶 ——
{
  clearFastlinkStreamCheckpoint();
  panApiGates.write.reset();
  panApiGates.write.hit();
  panApiGates.write.cooldownUntil = 0;
  panApiGates.write.strikes = 0;
  const api = makeApi();
  api.reuseFile = async (file) => {
    if (file.etag === MISS_ETAG) {
      const error = new Error("\u4E91\u7AEF\u6CA1\u6709\u53EF\u590D\u7528\u7684\u540C\u54C8\u5E0C\u6587\u4EF6");
      error.fastlinkMiss = true;
      throw error;
    }
    if (file.etag === "0e".repeat(16)) return "1";
    throw new Error("\u767B\u5F55\u5DF2\u8FC7\u671F");
  };
  const importer = createFastlinkStreamImporter(api, "0", {});
  await importer.consume([entry("good.mkv", "0e".repeat(16)), entry("bad.mkv", "0c".repeat(16)), entry("nope.mkv", MISS_ETAG)]);
  const result = await importer.finish();
  assert.equal(result.ok, 1, "成功的那条应计入成功桶");
  assert.equal(result.fail, 1, "真正的接口错误才算失败");
  assert.equal(result.miss, 1);
  assert.equal(result.status, "partial", "有成功也有真失败应标部分完成");
  const statuses = result.details.map((item) => item.status).sort().join(",");
  assert.equal(statuses, "failed,miss", "结果样本应同时包含失败与未命中两类");
  clearFastlinkStreamCheckpoint();
  console.log("ok 真失败与秒传未命中分列两类样本");
}

// —— 用例 4：续跑先还原限速门水位，不带着刚触发的风控满速再撞 ——
{
  clearFastlinkStreamCheckpoint();
  const seed = createFastlinkStreamImporter(makeApi(), "0", {});
  await seed.consume([entry("x.mkv", "0d".repeat(16)), entry("y.mkv", MISS_ETAG)]);
  await seed.finish();
  const record = readFastlinkStreamCheckpoint();
  const savedInterval = record.pacing.list.interval;
  panApiGates.list.reset();
  panApiGates.list.interval = 1;
  createFastlinkStreamImporter(makeApi(), "0", {});
  assert.equal(panApiGates.list.interval, savedInterval, "新建导入器时应把断点里的门水位还原回来");
  panApiGates.list.reset();
  clearFastlinkStreamCheckpoint();
  console.log("ok 续跑还原限速门水位（不带着旧惩罚满速再撞）");
}

console.log("fastlink-import-miss 全部通过");
