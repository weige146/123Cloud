// 元数据分段读取（Range）失败分类与重试回归（1.4.5）。
//
// 实测背景：8.7GB 大文件点「识别文件元数据」时报「下载服务器不支持分段读取：Range bytes=…
// 返回 HTTP 403（Accept-Ranges: 未声明）」，再点一次就好了——123 直链会偶发瞬时 403（时效/CDN 抖动），
// 旧代码把任何非 206 都断言成「不支持分段读取」且一次都不重试。这里钉住：
//   1）只有 2xx 未按 Range 返回 / 416 才说不支持分段或越界；
//   2）403/429/5xx/超时/网络错误算暂时性拒绝，退避后换新直链重试；
//   3）重试仍失败时给出可操作的提示，且失败窗口不占分片额度。
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const source = fs.readFileSync(fileURLToPath(new URL("../123-helper.user.js", import.meta.url)), "utf8");
const lines = source.split("\n");
const from = lines.findIndex((line) => line.includes("var NETWORK_WINDOW_SIZE ="));
const to = lines.findIndex((line) => line.includes("async function resolveMediaInfoWasmUrl()"));
assert.ok(from > 0 && to > from, "无法在脚本中定位元数据分段读取区段");
const code = lines.slice(from, to).join("\n");

const sandbox = { console, URL, TextDecoder, setTimeout, clearTimeout, Promise, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Error, Symbol, Boolean, Intl };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`${code}
globalThis.__reader = { rangeFailure, createRangeReader, preferAutomaticRedirect };`, sandbox, { filename: "123-helper.user.js" });
const { rangeFailure, createRangeReader } = sandbox.__reader;

const windowSize = 4 * 1024 * 1024;
const okResponse = (start, end) => ({
  status: 206,
  headers: { "content-range": `bytes ${start}-${end}/${end + 1}`, "accept-ranges": "bytes" },
  bytes: new Uint8Array(end - start + 1)
});
const failure = (status) => {
  const info = rangeFailure("https://cdn.example.com/file.mkv", 0, 100, status, { "accept-ranges": "" });
  return Object.assign(new Error(info.message), { retryable: info.retryable, status });
};

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test("失败分类：403 是暂时性拒绝、200 整包才是不支持分段、416 是范围越界", () => {
  const forbidden = rangeFailure("https://cdn.example.com/a.mkv", 10, 20, 403, {});
  assert.equal(forbidden.retryable, true, "403 应可重试");
  assert.ok(!/不支持分段读取/.test(forbidden.message), `403 不该断言不支持分段：${forbidden.message}`);
  assert.ok(/暂时拒绝/.test(forbidden.message) && /已过期/.test(forbidden.message), "403 文案应说明直链可能过期");
  for (const status of [429, 500, 502, 0]) {
    assert.equal(rangeFailure("https://cdn.example.com/a.mkv", 10, 20, status, {}).retryable, true, `${status} 应可重试`);
  }
  const whole = rangeFailure("https://cdn.example.com/a.mkv", 10, 20, 200, { "accept-ranges": "" });
  assert.equal(whole.retryable, false, "200 未按 Range 返回才是真不支持分段");
  assert.match(whole.message, /不支持分段读取/);
  const beyond = rangeFailure("https://cdn.example.com/a.mkv", 10, 20, 416, {});
  assert.equal(beyond.retryable, false, "416 重试也没意义");
  assert.match(beyond.message, /超出文件范围/);
});

test("尾部窗口 403：退避后换新直链重试并成功", async () => {
  const calls = [];
  sandbox.gmRangeRequest = async (url, start, end) => {
    calls.push({ url, start, end });
    if (calls.length === 1) throw failure(403);
    return okResponse(start, end);
  };
  let refreshed = 0;
  const fileSize = windowSize * 4;
  const reader = createRangeReader("https://cdn.example.com/a.mkv?sign=old", fileSize, null, null, {
    maxWindows: 3,
    refreshUrl: async () => {
      refreshed += 1;
      return "https://cdn.example.com/a.mkv?sign=fresh";
    }
  });
  const bytes = await reader(windowSize, fileSize - windowSize);
  assert.equal(bytes.byteLength, windowSize, "重试成功后应拿到完整分片");
  // 尾部正好一个分段：先 403 再重试成功 → 共 2 次请求
  assert.equal(calls.length, 2, `应只多打一次重试，实际 ${calls.length} 次`);
  assert.equal(calls[1].start, calls[0].start, "重试的是同一个分段");
  assert.equal(refreshed, 1, "重试前应先换一条新直链");
  assert.ok(calls[1].url.includes("sign=fresh"), "重试应走新直链");
});

test("一直 403：重试耗尽后给出可操作提示，不谎报「不支持分段读取」", async () => {
  let calls = 0;
  sandbox.gmRangeRequest = async () => {
    calls += 1;
    throw failure(403);
  };
  const reader = createRangeReader("https://cdn.example.com/a.mkv", windowSize * 2, null, null, {
    maxWindows: 3,
    refreshUrl: async () => "https://cdn.example.com/a.mkv?fresh"
  });
  await assert.rejects(() => reader(windowSize, 0), (error) => {
    assert.match(error.message, /暂时拒绝/, `文案应说明是暂时被拒：${error.message}`);
    assert.match(error.message, /已重试 2 次/, "应说明重试过");
    assert.match(error.message, /稍后再试|减少一次识别/, "应给出下一步怎么办");
    return true;
  });
  assert.equal(calls, 3, `共 1 + 2 次尝试，实际 ${calls}`);
});

test("真不支持分段（200 整包）不重试，直接一次定论", async () => {
  let calls = 0;
  sandbox.gmRangeRequest = async () => {
    calls += 1;
    throw failure(200);
  };
  const reader = createRangeReader("https://cdn.example.com/a.mkv", windowSize * 2, null, null, { maxWindows: 3 });
  await assert.rejects(() => reader(windowSize, 0), /不支持分段读取/);
  assert.equal(calls, 1, "不可重试的错误不该再打第二遍");
});

test("失败的分段不占媒体分片额度（后续分段仍可请求）", async () => {
  const starts = [];
  sandbox.gmRangeRequest = async (_url, start) => {
    starts.push(start);
    if (start === 0) throw failure(403);
    return okResponse(start, start + windowSize - 1);
  };
  const reader = createRangeReader("https://cdn.example.com/a.mkv", windowSize * 4, null, null, { maxWindows: 3, retries: 0 });
  const bytes = await reader(windowSize, windowSize * 2);
  assert.equal(bytes.byteLength, windowSize, "第一段失败后，第二段照常能读");
  assert.ok(starts.includes(windowSize * 2), "应请求到目标分段");
});

test("retries=0 时保持旧的一次即败（可被上层关掉重试）", async () => {
  let calls = 0;
  sandbox.gmRangeRequest = async () => {
    calls += 1;
    throw failure(429);
  };
  const reader = createRangeReader("https://cdn.example.com/a.mkv", windowSize, null, null, { maxWindows: 3, retries: 0 });
  await assert.rejects(() => reader(windowSize, 0), /暂时拒绝/);
  assert.equal(calls, 1);
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
console.log(`\nrange-reader-retry: 全部 ${cases.length} 个用例通过`);
