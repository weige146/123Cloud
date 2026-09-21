// 分享接口限速门回归测试（2026-09 风控实测参数的守护测试）：
// 1) 分享目录请求默认跨域直连 www.123865.com 且不带 cookie（mshare 分享域名配额仅约 60 个/分钟）；
// 2) 123865 返回网页 HTML（接口不可用）时自动退回页面自身域名并记住；
// 3) 收到 429"分享接口请求过于频繁"时限速门进入冷却、冷却结束自动续扫；
// 4) 风控状态写入扫描断点（pacing snapshot），续扫不立刻复触。
// 用法：node 油猴脚本/123-helper/tests/share-pacing.test.mjs
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
globalThis.__mod = { Pan123Api, shareApiGate, exportPublicShare, createFastlinkShareCheckpoint, readFastlinkShareCheckpoint, clearFastlinkShareCheckpoint, shareApiHostGet: () => shareApiHost, shareApiHostSet: (v) => { shareApiHost = v; } };
`;
const MSHARE_ORIGIN = "https://1849243207.mshare.123pan.cn";
const storageMap = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: MSHARE_ORIGIN },
  localStorage: {
    getItem: (key) => (storageMap.has(key) ? storageMap.get(key) : null),
    setItem: (key, value) => storageMap.set(key, String(value)),
    removeItem: (key) => storageMap.delete(key)
  },
  setTimeout, clearTimeout, AbortController, URL, URLSearchParams,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { Pan123Api, shareApiGate, exportPublicShare, createFastlinkShareCheckpoint, readFastlinkShareCheckpoint, clearFastlinkShareCheckpoint, shareApiHostGet, shareApiHostSet } = sandbox.__mod;

// 测试里把节奏调快：机制不变，只缩小时长参数（车道对象整个换掉，防 configureFor 覆盖）
function speedUpGate() {
  shareApiGate.fastLane = { baseInterval: 15, maxInterval: 300, jitterMs: 5 };
  shareApiGate.slowLane = { baseInterval: 15, maxInterval: 300, jitterMs: 5 };
  shareApiGate.reset();
  shareApiGate.baseInterval = 15;
  shareApiGate.interval = 15;
  shareApiGate.maxInterval = 300;
  shareApiGate.jitterMs = 5;
  shareApiGate.cooldownBaseMs = 300;
  shareApiGate.cooldownMaxMs = 1200;
}
speedUpGate();

const makeApi = (fetchImpl) => {
  const api = new Pan123Api({ host: MSHARE_ORIGIN, retryAttempts: 8 });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = fetchImpl;
  return api;
};
const jsonResponse = (payload) => ({ ok: true, status: 200, json: async () => payload });
const dirResponse = (list, next) => jsonResponse({ code: 0, message: "ok", data: { InfoList: list, Next: next } });
const folder = (id, name) => ({ FileId: id, FileName: name, Type: 1, Size: 0, Etag: "" });
const etag = (n) => String(n).padStart(2, "0").repeat(16);
const file = (id, name, size, checksum) => ({ FileId: id, FileName: name, Type: 0, Size: size, Etag: etag(checksum) });

// —— 用例 1：默认直连 www.123865.com 且不带 cookie ——
{
  speedUpGate();
  shareApiHostSet("");
  const calls = [];
  const api = makeApi((url, init) => {
    calls.push({ url: String(url), credentials: init?.credentials });
    if (calls.length === 1) return Promise.resolve(dirResponse([folder("f1", "剧集")], "-1"));
    return Promise.resolve(dirResponse([file("a", "01.mkv", 100, 2)], "-1"));
  });
  const files = await exportPublicShare(api, { shareKey: "k", sharePwd: "p" }, {});
  assert.equal(calls.length, 2, "根目录 + 子目录各一次请求");
  assert.ok(calls[0].url.startsWith("https://www.123865.com/b/api/share/get"), `应跨域直连 123865（实际 ${calls[0].url}）`);
  assert.equal(calls[0].credentials, "omit", "跨域直连 123865 必须不带 cookie（CORS 是 *）");
  assert.equal(files.fileCount, 1, "子目录文件应收集到");
  console.log("ok 分享目录请求默认直连 www.123865.com 并免 cookie");
}

// —— 用例 2：123865 返回 HTML 时退回页面自身域名（credentials 变回 include）并记住可用 host ——
{
  speedUpGate();
  shareApiHostSet("");
  const calls = [];
  let originCalls = 0;
  const api = makeApi((url, init) => {
    calls.push({ url: String(url), credentials: init?.credentials });
    if (String(url).startsWith("https://www.123865.com/")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => { throw new Error("HTML 不是 JSON"); } });
    }
    originCalls += 1;
    if (originCalls === 1) return Promise.resolve(dirResponse([folder("f1", "剧集")], "-1"));
    return Promise.resolve(dirResponse([file("a", "01.mkv", 100, 2)], "-1"));
  });
  const files = await exportPublicShare(api, { shareKey: "k", sharePwd: "p" }, {});
  assert.equal(files.fileCount, 1, "退回页面域名后应正常收集");
  assert.ok(originCalls >= 2, "页面自身域名应承接后续请求");
  const originCall = calls.find((call) => call.url.startsWith(`${MSHARE_ORIGIN}/`));
  assert.equal(originCall.credentials, "include", "同域请求保持带 cookie");
  assert.equal(shareApiHostGet(), MSHARE_ORIGIN, "会话内应记住可用的 host");
  console.log("ok 123865 不可用时自动退回页面域名并记住");
}

// —— 用例 3：429 限流时限速门冷却、冷却结束自动续扫成功 ——
{
  speedUpGate();
  shareApiHostSet("");
  let count = 0;
  const startedAt = Date.now();
  const api = makeApi((url) => {
    count += 1;
    if (count === 1) return Promise.resolve(jsonResponse({ code: "429", message: "分享接口请求过于频繁" }));
    if (count === 2) return Promise.resolve(dirResponse([folder("f1", "剧集")], "-1"));
    return Promise.resolve(dirResponse([file("a", "01.mkv", 100, 2)], "-1"));
  });
  const paceNotes = [];
  const files = await exportPublicShare(api, { shareKey: "k", sharePwd: "p" }, {
    onProgress: (_done, _total, message) => paceNotes.push(message)
  });
  const elapsed = Date.now() - startedAt;
  assert.equal(files.fileCount, 1, "429 冷却后自动续扫应成功");
  assert.ok(elapsed >= 250, `应等待过冷却（实际总耗时 ${elapsed}ms）`);
  assert.ok(shareApiGate.strikes >= 0 && shareApiGate.interval > 15, "触发风控后应放慢节奏");
  assert.ok(paceNotes.some((m) => m.includes("风控")), "冷却信息应通过进度回调透出");
  console.log(`ok 429 触发限速门冷却并自动续扫（冷却后总耗时 ${elapsed}ms）`);
}

// —— 用例 4：中断时风控状态随断点落盘（pacing snapshot），restore 往返不丢 ——
{
  speedUpGate();
  shareApiHostSet("");
  clearFastlinkShareCheckpoint();
  let count = 0;
  const api = makeApi((url) => {
    count += 1;
    if (count === 1) return Promise.resolve(jsonResponse({ code: "429", message: "分享接口请求过于频繁" }));
    if (count === 2) return Promise.resolve(dirResponse([folder("f1", "剧集"), file("r", "readme.txt", 10, 9)], "-1"));
    return Promise.reject(new Error("网络炸了"));
  });
  const input = { shareKey: "k", sharePwd: "p" };
  const checkpoint = createFastlinkShareCheckpoint(input, {});
  await assert.rejects(
    () => exportPublicShare(api, input, { checkpoint }),
    /网络炸了/,
    "后续网络故障应中断扫描（此时断点已落盘）"
  );
  const stored = readFastlinkShareCheckpoint();
  assert.ok(stored, "中断后断点应已写入");
  assert.ok(stored.pacing && Number(stored.pacing.strikes) >= 1, "断点里应记录风控触发次数");
  assert.ok(Number(stored.pacing.interval) > 15, "断点里应记录放慢后的间隔");
  assert.ok(stored.pending.some((entry) => entry.id === "f1"), "未扫完的子目录应在断点里");
  shareApiGate.reset();
  shareApiGate.restore(stored.pacing);
  assert.equal(shareApiGate.strikes, 1, "restore 后风控计数应保留");
  assert.equal(shareApiGate.interval, stored.pacing.interval, "restore 后节奏应保留");
  clearFastlinkShareCheckpoint();
  console.log("ok 风控状态随断点持久化并可 restore");
}

console.log("share-pacing 全部通过");
