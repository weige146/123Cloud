// 秒传接口线路（域名）策略回归：默认走页面同源域名且开跑前零探测、连撞频控才顺次换道、
// 旧 fastLane 兼容（只走镜像不换道）、手动测速结果 30 分钟内被用作首选与换道顺序、大分页降级。
// 用法：node 油猴脚本/123-helper/tests/drive-route.test.mjs
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
globalThis.__mod = {
  Pan123Api, probeDriveRoute, beginDriveRoute, noteRouteThrottle, driveRouteState,
  driveRouteLabel, DRIVE_ROUTE_KINDS, DRIVE_ROUTE_DEFAULT_ORDER, DRIVE_ROUTE_CACHE_KEY, panApiGates, fastlaneState
};
`;
const storageMap = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://www.123pan.cn" },
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
const { Pan123Api, probeDriveRoute, beginDriveRoute, noteRouteThrottle, driveRouteState, driveRouteLabel, DRIVE_ROUTE_KINDS, DRIVE_ROUTE_DEFAULT_ORDER, DRIVE_ROUTE_CACHE_KEY, panApiGates } = sandbox.__mod;

// 每条用例都把线路状态与测速缓存清干净，避免上一条留下的排序影响判断
const resetRoute = () => {
  storageMap.delete(DRIVE_ROUTE_CACHE_KEY);
  driveRouteState.order = [...DRIVE_ROUTE_DEFAULT_ORDER];
  driveRouteState.cursor = 0;
  driveRouteState.listLimit = 0;
  driveRouteState.probedAt = 0;
  driveRouteState.origin = "";
  driveRouteState.detail = {};
  driveRouteState.applied = "";
};

const pageOk = () => ({ ok: true, status: 200, json: async () => ({ code: 0, data: { InfoList: [{ FileId: 1, FileName: "a.mkv", Type: 0, Size: 1, Etag: "01" }], Next: "-1" } }) });
const throttled = () => ({ ok: true, status: 200, json: async () => ({ code: 100011, message: "" }) });
const broken = () => ({ ok: true, status: 200, json: async () => { throw new Error("不是 JSON"); } });

const makeApi = (handler) => {
  const api = new Pan123Api({ host: "https://www.123pan.cn" });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  api.calls = 0;
  sandbox.fetch = (url) => {
    api.calls += 1;
    return Promise.resolve(handler(String(url)));
  };
  return api;
};

// —— 用例 1：按可用性与频控排序，最宽松的线路排第一 ——
{
  const api = makeApi((url) => {
    if (url.startsWith(DRIVE_ROUTE_KINDS.mirror)) return throttled();
    if (url.startsWith(DRIVE_ROUTE_KINDS.canonical)) return pageOk();
    return pageOk();
  });
  const probe = await probeDriveRoute(api, {});
  assert.equal(probe.order[0], "canonical", "镜像线路撞频控时不该排在前面");
  assert.ok(!probe.order.includes(undefined), "排序结果应只包含已知线路");
  assert.equal(probe.detail.mirror.throttled, 2, "镜像撞限次数应记录");
  assert.equal(probe.listLimit, 500, "服务端吃下大分页时保持每页 500");
  console.log(`ok 探测按可用性/频控排序（${probe.order.join(" > ")}）`);
}

// —— 用例 2：探测结果可被「大分页不生效」降级 ——
{
  const api = makeApi(() => ({ ok: true, status: 200, json: async () => ({ code: 0, data: { InfoList: [], Next: "2" } }) }));
  const probe = await probeDriveRoute(api, {});
  assert.equal(probe.listLimit, 100, "没有条目也没有 -1 结束标记时，每页条数应降回 100");
  console.log("ok 大分页不被服务端吃下时自动降回每页 100 条");
}

// —— 用例 3：默认（没测过速）开跑前一个请求都不发，直接走页面同源域名 ——
{
  resetRoute();
  const api = makeApi(() => pageOk());
  const applied = await beginDriveRoute(api, {});
  assert.equal(api.calls, 0, "点了就该开跑：不得先发探测请求");
  assert.equal(applied.kind, "page", "默认线路是页面同源域名");
  assert.equal(applied.host, "");
  assert.equal(api.laneHost, "", "laneHost 留空即同源请求");
  assert.equal(applied.auto, true, "自动模式下保留撞限换道能力");
  console.log("ok 默认页面同源域名开跑，探测请求 0 次");
}

// —— 用例 4：页面域名连撞频控才换到 123865，再撞换镜像，换道后清零门水位 ——
{
  resetRoute();
  const api = makeApi(() => pageOk());
  await beginDriveRoute(api, {});
  panApiGates.list.hit();
  assert.ok(panApiGates.list.strikes >= 1);
  const strike = (host) => {
    for (let index = 0; index < 8; index += 1) if (noteRouteThrottle(api, host)) return true;
    return false;
  };
  assert.ok(strike(""), "同一线路连撞 6 次应换道");
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.canonical, `应换到 www.123865.com（实际 ${api.laneHost}）`);
  assert.equal(panApiGates.list.strikes, 0, "换道后应清零门的水位，按新车道档位重新起步");
  assert.ok(panApiGates.list.cooldownUntil <= Date.now(), true, "换道后不该继续背着旧冷却");
  assert.ok(strike(DRIVE_ROUTE_KINDS.canonical), "第二条线路撞满也该继续换");
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.mirror, "再撞换到镜像域名");
  assert.equal(driveRouteLabel("page"), "页面域名");
  console.log("ok 连撞频控按 页面域名 → 123865 → 镜像 顺次换道");
}

// —— 用例 5：30 分钟内手动测过速，就按测出来的排序开跑 ——
{
  resetRoute();
  const api = makeApi((url) => {
    if (url.startsWith(DRIVE_ROUTE_KINDS.mirror)) return throttled();
    return pageOk();
  });
  const probe = await probeDriveRoute(api, {});
  assert.equal(probe.order[0], "canonical", "本例里 123865 最快且不限流");
  const before = api.calls;
  const applied = await beginDriveRoute(api, {});
  assert.equal(api.calls, before, "有测速结果时开跑也不重复探测");
  assert.equal(applied.kind, "canonical", "采用测速排序的第一条");
  assert.equal(applied.probed, true);
  console.log("ok 手动测速结果被复用为首选线路");
}

// —— 用例 6：旧 fastLane 配置仍只走镜像域名、不被自动换道改写 ——
{
  resetRoute();
  const api = makeApi(() => pageOk());
  const applied = await beginDriveRoute(api, { fastLane: true });
  assert.equal(applied.kind, "mirror", "旧 fastLane 开关应等价映射为直连镜像域名");
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.mirror);
  assert.equal(applied.auto, false, "旧开关不该自动换道");
  for (let index = 0; index < 12; index += 1) {
    assert.equal(noteRouteThrottle(api, DRIVE_ROUTE_KINDS.mirror), false, "旧开关撞限也不换道");
  }
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.mirror, "旧开关撞限后仍停在镜像域名");
  console.log("ok 旧 fastLane 配置只走镜像、不被自动换道改写");
}

// —— 用例 7：换道顺序走完一圈后回到第一条继续轮转（不会卡死在没人用的线路上）——
{
  resetRoute();
  const api = makeApi(() => pageOk());
  await beginDriveRoute(api, {});
  const strike = (host) => {
    for (let index = 0; index < 8; index += 1) if (noteRouteThrottle(api, host)) return true;
    return false;
  };
  strike("");
  strike(DRIVE_ROUTE_KINDS.canonical);
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.mirror);
  assert.ok(strike(DRIVE_ROUTE_KINDS.mirror), "最后一条撞满时回到第一条继续轮转");
  assert.equal(api.laneHost, "", "轮转回页面同源域名继续跑");
  console.log("ok 换道顺序走完一圈后轮转回页面域名");
}

console.log("drive-route 全部通过");
