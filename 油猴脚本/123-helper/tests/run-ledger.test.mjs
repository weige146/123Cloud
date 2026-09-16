// 秒传运行明细账本 + 结果页筛选/CSV 回归：计数与速率、日志封顶不失控、明细转义、
// 未命中样本可筛选可导出（千万级任务不能把日志整表驻留，也不能把 <script> 塞进 DOM）。
// 用法：node 油猴脚本/123-helper/tests/run-ledger.test.mjs
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
  "function icon(name) { return `<i data-icon=\"${name}\"></i>`; }",
  slice("// src/ui/components.js", "// src/ui/views/cleaner.js")
].join("\n");
const driver = `;
globalThis.__mod = { createRunLedger, filterResultDetails, buildResultCsv, resultTable, statusLabel, formatBytes, durationText, truncateMiddle };
`;
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://www.123865.com" },
  setTimeout, clearTimeout, AbortController,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { createRunLedger, filterResultDetails, buildResultCsv, resultTable, statusLabel, durationText, truncateMiddle } = sandbox.__mod;

// —— 用例 1：扫描明细计数 + 摘要一行式透出 ——
{
  const ledger = createRunLedger({ limit: 50 });
  for (let index = 0; index < 5; index += 1) {
    ledger.detail({ kind: "file", name: `f${index}.mkv`, path: `剧/f${index}.mkv`, size: 1000, files: index + 1, folders: 1, bytes: (index + 1) * 1000 });
  }
  ledger.detail({ kind: "folder", path: "剧/season1", name: "season1", entries: 12, folders: 2, files: 5, bytes: 5000 });
  const stats = ledger.stats();
  assert.equal(stats.counters.files, 5, "文件计数应取扫描侧最大值");
  assert.equal(stats.counters.folders, 2, "目录计数应跟上");
  assert.equal(stats.counters.bytes, 5000, "累计体积不该重复累加");
  const summary = ledger.summary("扫描秒传文件");
  assert.ok(summary.includes("\u76EE\u5F55 2") && summary.includes("\u6587\u4EF6 5"), `摘要应含目录与文件计数：${summary}`);
  assert.ok(/KB|MB|GB/.test(summary), `摘要应含体积：${summary}`);
  console.log(`ok 扫描明细计数与摘要（${summary}）`);
}

// —— 用例 2：日志封顶（紧凑模式不逐条刷文件行） ——
{
  const ledger = createRunLedger({ limit: 30 });
  for (let index = 0; index < 500; index += 1) {
    ledger.detail({ kind: "file", path: `p/${index}.mkv`, size: 10, files: index + 1, folders: 1, bytes: (index + 1) * 10 });
    ledger.detail({ kind: "folder", path: `p/${index}`, entries: 3, folders: index + 1, files: index + 1, bytes: (index + 1) * 10 });
  }
  assert.ok(ledger.lines.length <= 30, `紧凑模式日志应封顶 30 行（实际 ${ledger.lines.length}）`);
  assert.ok(!ledger.lines.some((line) => line.text.startsWith("\u6587\u4EF6 ")), "紧凑模式不该把每条文件写进日志");
  ledger.setVerbose(true);
  for (let index = 0; index < 500; index += 1) ledger.detail({ kind: "file", path: `q/${index}.mkv`, size: 10, files: 600 + index, folders: 2, bytes: 6000 });
  assert.ok(ledger.lines.length <= 800, `展开模式日志也应封顶（实际 ${ledger.lines.length}）`);
  console.log(`ok 日志环形封顶（紧凑 ${30} 行 / 展开上限 800 行）`);
}

// —— 用例 3：频控/暂停/换道通知进摘要，且带冷却倒计时文案 ——
{
  const ledger = createRunLedger({});
  ledger.pace({ seconds: 12, strikes: 2, gate: "list" });
  assert.ok(ledger.stats().note.includes("12"), "冷却倒计时应进摘要");
  assert.ok(ledger.summary("").includes("\u9891\u63A7\u51B7\u5374"), "摘要应显示频控冷却提示");
  ledger.pace({ paused: true });
  assert.ok(ledger.stats().note.includes("\u6682\u505C"), "暂停状态应进摘要");
  ledger.pace({ routeChanged: "mirror" });
  assert.equal(ledger.stats().note, "", "换道后清掉旧的等待提示");
  assert.ok(ledger.lines.some((line) => line.text.includes("api.123278.com")), "换道应记一条明细");
  console.log("ok 频控冷却/暂停/自动换道都能透出");
}

// —— 用例 4：导入明细分桶 + 渲染转义 ——
{
  const ledger = createRunLedger({ limit: 40 });
  ledger.detail({ kind: "import", status: "success", path: "a.mkv", size: 2048, etag: "01234567", ok: 1, fail: 0, miss: 0, bytes: 2048 });
  ledger.detail({ kind: "import", status: "miss", path: "b.mkv", size: 4096, ok: 1, fail: 0, miss: 1, bytes: 2048 });
  ledger.detail({ kind: "import", status: "failed", path: "<img onerror=alert(1)>.mkv", size: 1, message: "\u767B\u5F55\u5DF2\u8FC7\u671F", ok: 1, fail: 1, miss: 1, bytes: 2048 });
  const stats = ledger.stats();
  assert.equal(stats.counters.ok, 1);
  assert.equal(stats.counters.miss, 1, "未命中应单独计数");
  assert.equal(stats.counters.fail, 1);
  assert.equal(stats.counters.bytes, 2048, "体积按已成功条目累计");
  const html = ledger.render(40);
  assert.ok(!html.includes("<img onerror"), "明细渲染必须转义");
  assert.ok(html.includes("&lt;img onerror"), "转义后文本仍可读");
  console.log("ok 导入明细分桶正确且渲染安全");
}

// —— 用例 5：结果页筛选与 CSV（未命中可单独筛出来导出） ——
{
  const result = {
    ok: 2,
    fail: 1,
    miss: 1,
    skipped: 3,
    bytes: 4096,
    details: [
      { path: "a.mkv", status: "success", size: 10 },
      { path: "b.mkv", status: "failed", message: "\u8D85\u65F6", size: 20 },
      { path: "c,\"d\".mkv", status: "miss", message: "\u4E91\u7AEF\u65E0\u540C\u54C8\u5E0C", size: 30 }
    ]
  };
  assert.equal(filterResultDetails(result, "all").length, 3);
  assert.equal(filterResultDetails(result, "failed").length, 1, "只筛真失败");
  assert.equal(filterResultDetails(result, "miss").length, 1, "未命中可单独筛");
  assert.equal(filterResultDetails(result, "success").length, 1);
  assert.equal(statusLabel("miss"), "\u672A\u547D\u4E2D");
  const csv = buildResultCsv(result, "all");
  assert.ok(csv.startsWith("\uFEFF"), "CSV 应带 BOM（Excel 不乱码）");
  assert.ok(csv.includes('"c,""d"".mkv"'), "含逗号与引号的路径应正确转义");
  assert.ok(csv.includes("\u672A\u547D\u4E2D"), "CSV 状态列应显示未命中");
  const onlyMiss = buildResultCsv(result, "miss");
  assert.equal(onlyMiss.trim().split("\n").length, 2, "按未命中筛选后 CSV 只应有一行数据");
  const html = resultTable(result, 1, "miss");
  assert.ok(html.includes("\u672A\u547D\u4E2D"), "结果页应显示未命中统计");
  assert.ok(html.includes('id="result-filter"'), "结果页应有筛选控件");
  assert.ok(html.includes("data-action=\"result-csv\""), "结果页应有 CSV 导出按钮");
  assert.ok(/4 KB|4096/.test(html), `应显示已转存体积：${html.match(/\u5DF2\u8F6C\u5B58\u4F53\u79EF<\/span><strong>[^<]*/)?.[0]}`);
  console.log("ok 结果页筛选与 CSV 导出（含未命中桶与体积）");
}

// —— 用例 6：速率统计含目录（实测媒体库扫描目录远多于文件），总量未知时不猜剩余时间 ——
// 注意：账本内部用真实毫秒时间戳，本用例必须跑在真实时钟上（不要替换沙箱里的 sleep）
{
  const ledger = createRunLedger({ limit: 20 });
  ledger.progress(0, 1, "\u626b\u63cf\u79d2\u4f20\u6587\u4ef6");
  const pushFolder = (index) => ledger.detail({ kind: "folder", path: `d${index}`, name: `d${index}`, entries: 1, folders: index + 1, files: 2, bytes: (index + 1) * 1024 });
  for (let index = 0; index < 20; index += 1) pushFolder(index);
  await new Promise((resolve) => setTimeout(resolve, 700));
  for (let index = 20; index < 40; index += 1) pushFolder(index);
  const stats = ledger.stats();
  assert.ok(stats.rate > 5, `40 个目录应算进速率（实际 ${stats.rate.toFixed(2)} 项/秒）`);
  assert.equal(stats.etaSec, 0, "\u603b\u91cf\u672a\u77e5\u65f6\u4e0d\u5e94\u731c\u5269\u4f59\u65f6\u95f4");
  assert.ok(!ledger.summary("").includes("\u5269\u4f59\u7ea6"), `\u603b\u91cf\u672a\u77e5\u65f6\u6458\u8981\u4e0d\u8be5\u6709\u5269\u4f59\u65f6\u95f4\uff1a${ledger.summary("")}`);
  ledger.progress(20, 200, "\u5bfc\u5165\u79d2\u4f20");
  for (let index = 0; index < 15; index += 1) ledger.detail({ kind: "import", status: "success", path: `f${index}`, size: 10, ok: index + 1, fail: 0, miss: 0, bytes: (index + 1) * 10 });
  await new Promise((resolve) => setTimeout(resolve, 700));
  for (let index = 15; index < 30; index += 1) ledger.detail({ kind: "import", status: "success", path: `f${index}`, size: 10, ok: index + 1, fail: 0, miss: 0, bytes: (index + 1) * 10 });
  const imported = ledger.stats();
  assert.ok(imported.rate > 0, "\u5bfc\u5165\u901f\u7387\u5e94\u6309\u6210\u529f\u6570\u7edf\u8ba1");
  console.log(`ok \u901f\u7387\u542b\u76ee\u5f55\u7edf\u8ba1\u3001\u603b\u91cf\u672a\u77e5\u4e0d\u731c ETA\uff08\u5bfc\u5165\u4fa7 ${imported.rate.toFixed(1)} \u9879/\u79d2\uff09`);
}

// —— 用例 7：时长与长路径文案辅助 ——
{
  assert.equal(durationText(30), "30 \u79D2");
  assert.equal(durationText(600), "10 \u5206\u949F");
  assert.equal(durationText(7260), "2 \u5C0F\u65F6 1 \u5206");
  const long = `${"目".repeat(40)}/${"文".repeat(40)}.mkv`;
  const cut = truncateMiddle(long, 30);
  assert.ok(cut.length <= 31 && cut.includes("\u2026"), "长路径应中间省略");
  assert.ok(cut.endsWith("\u6587.mkv".replace("\u6587", "\u6587")), "应保留结尾（文件名比开头更有辨识度）");
  console.log("ok 剩余时间与长路径文案辅助正常");
}

console.log("run-ledger 全部通过");
