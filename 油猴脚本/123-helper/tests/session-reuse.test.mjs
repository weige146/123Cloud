// 登录会话导出/导入（跨浏览器复用，不新增设备）回归测试。
// 背景：脚本每个浏览器登录都会在 123 云盘新增一台设备。1.3.8 起支持把当前浏览器的
// authorToken + LoginUuid（localStorage 原始值，LoginUuid 可能是站点加密形态）导出为
// JSON，在其他浏览器原样写回并刷新，官方 SPA 与脚本共用同一会话。
// 入口：设置 → 常规 →「登录会话复用」按钮；油猴菜单「导出/导入登录会话」（登录页也能用）。
// 用法：node 油猴脚本/123-helper/tests/session-reuse.test.mjs
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
const store = new Map();
const gmStore = new Map();
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123pan.cn", hostname: "www.123pan.cn" },
  URL,
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key)
  },
  GM_setValue: (key, value) => gmStore.set(key, String(value)),
  GM_getValue: (key, fallback) => (gmStore.has(key) ? gmStore.get(key) : fallback)
};
vm.createContext(sandbox);
vm.runInContext([slice("// src/core/utils.js", "// src/api.js"), slice("// src/api.js", "// src/core/categories.js")].join("\n"), sandbox, { filename: "123-helper.user.js" });
vm.runInContext(`
globalThis.__sessionMod = { buildSessionExportPayload, parseSessionImportPayload, applySessionImportPayload, resolveSessionImportPlan, completeSessionImport, consumePendingSessionHandoff, SESSION_HANDOFF_KEY };
`, sandbox, { filename: "driver.js" });
const { buildSessionExportPayload, parseSessionImportPayload, applySessionImportPayload, resolveSessionImportPlan, completeSessionImport, consumePendingSessionHandoff, SESSION_HANDOFF_KEY } = sandbox.__sessionMod;

// —— 1. 已登录：导出 localStorage 原始值并写回（往返一致） ——
{
  store.set("authorToken", "eyJhbGciOi.encrypted.token.value");
  store.set("LoginUuid", "a3f1c2b4"); // 站点加密形态也原样导出/写回
  const payload = JSON.parse(buildSessionExportPayload());
  assert.equal(payload.c123Session, 1);
  assert.equal(payload.authorToken, "eyJhbGciOi.encrypted.token.value");
  assert.equal(payload.LoginUuid, "a3f1c2b4");

  store.set("authorToken", "old");
  store.set("LoginUuid", "old-uuid");
  applySessionImportPayload(JSON.stringify(payload));
  assert.equal(store.get("authorToken"), "eyJhbGciOi.encrypted.token.value");
  assert.equal(store.get("LoginUuid"), "a3f1c2b4");
  console.log("ok 导出/导入往返：localStorage 原始值原样写回");
}

// —— 2. 容错解析：竖线分隔、两行分隔也能导入 ——
{
  const parsed = parseSessionImportPayload("token-abc|uuid-def");
  assert.deepEqual({ ...parsed }, { authorToken: "token-abc", loginUuid: "uuid-def" });
  const parsedLines = parseSessionImportPayload("token-abc\nuuid-def\n");
  assert.deepEqual({ ...parsedLines }, { authorToken: "token-abc", loginUuid: "uuid-def" });
  console.log("ok 容错解析：token|uuid 与两行格式均可导入");
}

// —— 3. 非法输入：空、残缺 JSON、单 token 无 uuid → 明确报错，不写 localStorage ——
{
  store.set("authorToken", "keep");
  store.set("LoginUuid", "keep-uuid");
  assert.throws(() => applySessionImportPayload(""), /请粘贴/);
  assert.throws(() => applySessionImportPayload("只有token没有uuid"), /不完整/);
  assert.throws(() => applySessionImportPayload(JSON.stringify({ foo: 1 })), /不完整/);
  assert.equal(store.get("authorToken"), "keep", "失败导入不应改动现有会话");
  assert.equal(store.get("LoginUuid"), "keep-uuid");
  console.log("ok 非法输入：明确报错且不破坏现有会话");
}

// —— 4. 未登录导出：明确报错 ——
{
  store.clear();
  assert.throws(() => buildSessionExportPayload(), /没有可导出/);
  console.log("ok 未登录导出：明确提示先登录");
}

// —— 5. 导入计划：网盘页直写；登录页/分享页记住并跳「来源网盘页」（无来源缺省 www.123pan.cn）——
{
  assert.deepEqual({ ...resolveSessionImportPlan("www.123pan.cn") }, { applyHere: true, target: "" });
  assert.deepEqual({ ...resolveSessionImportPlan("yun.123pan.cn") }, { applyHere: true, target: "" });
  assert.deepEqual({ ...resolveSessionImportPlan("user.123pan.cn", "https://yun.123pan.cn") }, { applyHere: false, target: "https://yun.123pan.cn" });
  assert.equal(resolveSessionImportPlan("user.123pan.cn").target, "https://www.123pan.cn");
  assert.equal(resolveSessionImportPlan("abc.mshare.123pan.cn", "https://evil.example.com").target, "https://www.123pan.cn", "非网盘域名的来源不采纳");
  console.log("ok 导入计划：网盘页直写；其他页跳来源网盘页（白名单校验）");
}

// —— 6. 登录页拉取：不写本页（写了没用），跳来源网盘页自动写回登录 ——
{
  store.clear();
  sandbox.location.hostname = "user.123pan.cn";
  const plan = completeSessionImport("token-x|uuid-y", "https://yun.123pan.cn");
  assert.equal(plan.applyHere, false, "登录页 localStorage 不承载网盘会话，不写入");
  assert.equal(plan.target, "https://yun.123pan.cn", "跳转到推送来源的网盘页");
  assert.equal(store.has("authorToken"), false);
  assert.ok(gmStore.get(SESSION_HANDOFF_KEY)?.includes("token-x"), "会话暂存进油猴存储");

  // 到达来源网盘页：消费接力 → 写入 + 清暂存；重复消费不再触发
  assert.equal(consumePendingSessionHandoff("yun.123pan.cn"), true);
  assert.equal(store.get("authorToken"), "token-x");
  assert.equal(store.get("LoginUuid"), "uuid-y");
  assert.equal(consumePendingSessionHandoff("yun.123pan.cn"), false);

  // 已是同一会话（如页面自身已带凭据）→ 只清暂存、不重复刷新
  gmStore.set(SESSION_HANDOFF_KEY, JSON.stringify({ authorToken: "token-x", loginUuid: "uuid-y", ts: Date.now() }));
  assert.equal(consumePendingSessionHandoff("yun.123pan.cn"), false);
  assert.equal(gmStore.get(SESSION_HANDOFF_KEY), "");
  console.log("ok 登录页拉取：暂存并跳来源网盘页；写入/同会话跳过均正确");
}

// —— 7. 接力过期与入口防护：过期暂存不写入；非网盘页不消费 ——
{
  const stale = JSON.stringify({ authorToken: "old", loginUuid: "old-u", ts: Date.now() - 11 * 60 * 1000 });
  sandbox.location.hostname = "www.123pan.cn";
  gmStore.set(SESSION_HANDOFF_KEY, stale);
  assert.equal(consumePendingSessionHandoff("www.123pan.cn"), false);
  assert.equal(store.get("authorToken"), "token-x", "过期接力不应改写会话");
  assert.equal(gmStore.get(SESSION_HANDOFF_KEY), "", "过期接力应清空");

  sandbox.location.hostname = "user.123pan.cn";
  gmStore.set(SESSION_HANDOFF_KEY, JSON.stringify({ authorToken: "t", loginUuid: "u", ts: Date.now() }));
  assert.equal(consumePendingSessionHandoff("user.123pan.cn"), false);
  sandbox.location.hostname = "www.123pan.cn";
  console.log("ok 接力防护：过期不写入；只在网盘页消费");
}

// —— 8. 网盘页导入：直接写入，不走暂存 ——
{
  sandbox.location.hostname = "www.123pan.cn";
  gmStore.clear();
  const plan = completeSessionImport(JSON.stringify({ c123Session: 1, authorToken: "direct", LoginUuid: "direct-u" }));
  assert.equal(plan.applyHere, true);
  assert.equal(store.get("authorToken"), "direct");
  assert.equal(gmStore.has(SESSION_HANDOFF_KEY), false, "网盘页导入不需要暂存");
  sandbox.location.hostname = "www.123pan.cn";
  console.log("ok 网盘页导入：直接写入不暂存");
}
