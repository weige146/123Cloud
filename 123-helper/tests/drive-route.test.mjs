// 秒传接口线路（域名）策略回归：探测排序、大分页降级、旧 fastLane 兼容、自动模式下连续撞限换道。
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
  driveRouteLabel, DRIVE_ROUTE_KINDS, panApiGates, fastlaneState
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
const { Pan123Api, probeDriveRoute, beginDriveRoute, noteRouteThrottle, driveRouteState, driveRouteLabel, DRIVE_ROUTE_KINDS, panApiGates } = sandbox.__mod;

const pageOk = () => ({ ok: true, status: 200, json: async () => ({ code: 0, data: { InfoList: [{ FileId: 1, FileName: "a.mkv", Type: 0, Size: 1, Etag: "01" }], Next: "-1" } }) });
const throttled = () => ({ ok: true, status: 200, json: async () => ({ code: 100011, message: "" }) });
const broken = () => ({ ok: true, status: 200, json: async () => { throw new Error("不是 JSON"); } });

const makeApi = (handler) => {
  const api = new Pan123Api({ host: "https://www.123pan.cn" });
  api.credentials = () => ({ token: "t", loginUuid: "u" });
  sandbox.fetch = (url) => Promise.resolve(handler(String(url)));
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

// —— 用例 3：设置页没有手动选路，旧 fastLane 配置仍只走镜像域名、不自动换道 ——
{
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

// —— 用例 4：默认（自动）模式下同一线路连续撞限换到下一候选并重置门水位 ——
{
  const api = makeApi(() => pageOk());
  driveRouteState.order = ["canonical", "mirror", "page"];
  driveRouteState.cursor = 0;
  driveRouteState.probedAt = Date.now();
  driveRouteState.origin = "https://www.123pan.cn";
  const applied = await beginDriveRoute(api, {});
  assert.equal(applied.kind, "canonical", "默认应使用探测排序的第一条");
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.canonical);
  panApiGates.list.hit();
  assert.ok(panApiGates.list.strikes >= 1);
  let switched = false;
  for (let index = 0; index < 8 && !switched; index += 1) {
    switched = noteRouteThrottle(api, DRIVE_ROUTE_KINDS.canonical);
  }
  assert.ok(switched, "连续撞限应触发换道");
  assert.equal(api.laneHost, DRIVE_ROUTE_KINDS.mirror, `应换到下一条候选线路（实际 ${api.laneHost}）`);
  assert.equal(panApiGates.list.strikes, 0, "换道后应清零门的水位，按新车道档位重新起步");
  assert.equal(panApiGates.list.cooldownUntil <= Date.now(), true, "换道后不该继续背着旧冷却");
  assert.equal(driveRouteLabel("page"), "页面域名");
  console.log("ok 自动模式撞限自动换道并重置限速门水位");
}

console.log("drive-route 全部通过");
