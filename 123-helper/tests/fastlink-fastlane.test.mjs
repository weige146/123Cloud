// 秒传接口线路（api.123278.com 直连）回归测试。
// 背景：维护者 2026-09-14 实测 upload_request 在 https://api.123278.com 16~24 线程可达 ~150 次/秒，
// 高于默认域名 www.123865.com 的 32 并发 85 次/秒。1.3.8 起秒传设置可开「fastLane」：
// 导入/导出任务窗口内 reuseFile/createFolder/listPage 直连镜像域名（鉴权 Bearer 头，cookie omit），
// request() 内置网络级故障回退——镜像连不通/CORS 拦/403/404/501/超时都当场摘除线路并
// 同一轮改打默认域名，不烧重试次数；并发在镜像线路下按配置 ≥16 生效、否则默认 24。
// 用法：node 油猴脚本/123-helper/tests/fastlink-fastlane.test.mjs
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
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, WeakMap, RegExp, Intl, Symbol, Error, TypeError,
  DOMException, BigInt, Boolean, Promise, Float64Array,
  TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123pan.cn" },
  setTimeout, clearTimeout, AbortController, URL, URLSearchParams, MessageChannel: globalThis.MessageChannel,
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: "123-helper.user.js" });
vm.runInContext(`
globalThis.__fastlinkLane = { Pan123Api, importFastlink, FASTLANE_HOST, fastlaneState, panApiGates };
`, sandbox, { filename: "driver.js" });
const { Pan123Api, importFastlink, FASTLANE_HOST, fastlaneState, panApiGates } = sandbox.__fastlinkLane;

// 本文件考的是「打到哪个域名、cookie、重试次数」，不该被新的全局限速门排队干扰：
// 每个用例前把间距调成 0、清掉冷却与排队时隙（门的机制由 gate-pacing / request-gate 两个测试覆盖）。
const resetGates = () => {
  for (const gate of Object.values(panApiGates)) {
    gate.lanes = {
      default: { baseInterval: 0, maxInterval: 0, jitterMs: 0 },
      page: { baseInterval: 0, maxInterval: 0, jitterMs: 0 },
      canonical: { baseInterval: 0, maxInterval: 0, jitterMs: 0 },
      mirror: { baseInterval: 0, maxInterval: 0, jitterMs: 0 }
    };
    gate.cooldownUntil = 0;
    gate.nextFree = 0;
    gate.reset();
  }
};

const makeApi = (fetchImpl) => {
  resetGates();
  const api = new Pan123Api({ host: "https://www.123pan.cn", retryAttempts: 4 });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = fetchImpl;
  return api;
};
const okJson = (body) => ({ ok: true, status: 200, json: async () => ({ code: 0, data: body }) });
const etag = (n) => String(n).padStart(8, "0").repeat(4);

// —— 1. 线路开启：请求打镜像域名、cookie omit、鉴权头保留 ——
{
  const urls = [];
  const api = makeApi((url, init) => {
    urls.push({ url: String(url), credentials: init.credentials, auth: Boolean(init.headers.authorization) });
    return Promise.resolve(okJson({ InfoList: [], Next: "-1" }));
  });
  api.laneHost = FASTLANE_HOST;
  await api.listPage("0", 1);
  assert.equal(urls.length, 1);
  assert.ok(urls[0].url.startsWith(`${FASTLANE_HOST}/b/api/file/list/new`), `应直连镜像域名，实际 ${urls[0].url}`);
  assert.equal(urls[0].credentials, "omit", "镜像域名跨域必须 omit cookie");
  assert.equal(urls[0].auth, true, "Bearer 鉴权头必须保留");
  console.log("ok 线路开启：listPage 直连 api.123278.com 且 cookie omit");
}

// —— 2. 线路关闭：行为与旧版一致（页面域名） ——
{
  const urls = [];
  const api = makeApi((url) => {
    urls.push(String(url));
    return Promise.resolve(okJson({ InfoList: [], Next: "-1" }));
  });
  await api.listPage("0", 1);
  assert.ok(urls[0].startsWith("https://www.123pan.cn/"), `默认应打页面域名，实际 ${urls[0]}`);
  console.log("ok 线路关闭：默认域名行为不变");
}

// —— 3. 镜像网络层失败：同一轮回退默认域名、不烧重试，线路会话内摘除 ——
{
  const urls = [];
  const api = makeApi((url) => {
    urls.push(String(url));
    if (String(url).startsWith(FASTLANE_HOST)) return Promise.reject(new TypeError("Failed to fetch"));
    return Promise.resolve(okJson({ InfoList: [], Next: "-1" }));
  });
  api.laneHost = FASTLANE_HOST;
  const page = await api.listPage("0", 1);
  assert.equal(page.files.length, 0);
  assert.deepEqual(urls.map((url) => new URL(url).origin), [new URL(FASTLANE_HOST).origin, "https://www.123pan.cn"], "同一轮先镜像后默认");
  assert.equal(fastlaneState.dead, true, "镜像失败应摘除线路");
  assert.equal(api.laneHost, "", "api.laneHost 应被清空");
  urls.length = 0;
  await api.listPage("0", 1);
  assert.ok(urls[0].startsWith("https://www.123pan.cn/"), "摘除后后续请求直接走默认域名");
  fastlaneState.dead = false;
  console.log("ok 线路故障回退：同一轮切换默认域名、会话内摘除、重试次数不烧");
}

// —— 4. 镜像 404（不服务该路径）：同样摘除线路回退 ——
{
  const api = makeApi((url) => {
    if (String(url).startsWith(FASTLANE_HOST)) return Promise.resolve({ ok: false, status: 404, json: async () => ({ message: "not found" }) });
    return Promise.resolve(okJson({ InfoList: [], Next: "-1" }));
  });
  api.laneHost = FASTLANE_HOST;
  await api.listPage("0", 1);
  assert.equal(fastlaneState.dead, true);
  fastlaneState.dead = false;
  console.log("ok 镜像 404：摘除线路回退默认域名");
}

// —— 5. 用户取消：镜像上 AbortError 原样上抛，不回退不打默认域名 ——
{
  const urls = [];
  const api = makeApi((url, init) => {
    urls.push(String(url));
    return new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("操作已取消", "AbortError")));
    });
  });
  api.laneHost = FASTLANE_HOST;
  const controller = new AbortController();
  const pending = api.listPage("0", 1, { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error) => error.name === "AbortError");
  // 取消可能发生在取号阶段（限速门），所以只看「有没有偷打默认域名」
  assert.ok(urls.length <= 1, `取消后不该继续发请求（实际 ${urls.length} 次）`);
  assert.ok(!urls.some((url) => !url.startsWith(FASTLANE_HOST)), "用户取消不该回退打默认域名");
  assert.equal(fastlaneState.dead, false, "用户取消不算镜像故障");
  console.log("ok 用户取消：原样中止，不误判线路故障");
}

// —— 5b. 镜像超时（无 signal 的 AbortError）：按线路故障处理，回退默认域名 ——
{
  const urls = [];
  const api = makeApi((url) => {
    urls.push(String(url));
    if (String(url).startsWith(FASTLANE_HOST)) return Promise.reject(new DOMException("超时中止", "AbortError"));
    return Promise.resolve(okJson({ InfoList: [], Next: "-1" }));
  });
  api.laneHost = FASTLANE_HOST;
  await api.listPage("0", 1);
  assert.deepEqual(urls.map((url) => new URL(url).origin), [new URL(FASTLANE_HOST).origin, "https://www.123pan.cn"]);
  assert.equal(fastlaneState.dead, true, "镜像挂起/超时应摘除线路");
  fastlaneState.dead = false;
  console.log("ok 镜像超时：按线路故障回退默认域名");
}

// —— 6. 导入并发：镜像线路未配置默认 24、配置 ≥16 生效；默认线路保持 32 底 ——
{
  const makeStubApi = (lane) => {
    let inFlight = 0;
    const peaks = [];
    const track = () => {
      inFlight += 1;
      peaks.push(inFlight);
      inFlight -= 1;
      return inFlight;
    };
    void track;
    return {
      laneHost: lane ? FASTLANE_HOST : "",
      peak: 0,
      calls: { reuse: 0 },
      async findChildFolder() {
        return null;
      },
      async createFolder(parentId, name) {
        return `dir-${parentId}-${name}`;
      },
      async ensurePath(rootId2, parts) {
        return `dir-${[rootId2, ...parts].join("/")}`;
      },
      async reuseFile() {
        this.calls.reuse += 1;
        inFlight += 1;
        this.peak = Math.max(this.peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 3));
        inFlight -= 1;
        return `id-${this.calls.reuse}`;
      }
    };
  };
  const files = Array.from({ length: 48 }, (_, index) => ({ path: `文件${index}.mp4`, fileName: `文件${index}.mp4`, etag: etag(index + 1), size: index }));
  const runCase = async (lane, concurrency) => {
    const api = makeStubApi(lane);
    await importFastlink(api, { commonPath: "", files }, "root", { concurrency });
    return api.peak;
  };
  assert.ok(await runCase(false, undefined) >= 32, "默认线路并发应保持 32 底");
  assert.ok(await runCase(true, undefined) >= 20, "镜像线路默认并发应到 24 附近");
  assert.ok(await runCase(true, 48) >= 40, "镜像线路配置 48 应生效");
  console.log("ok 并发策略：默认线路 32 底、镜像线路 24 默认且配置可生效");
}
