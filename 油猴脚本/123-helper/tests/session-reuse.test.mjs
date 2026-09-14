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
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  location: { origin: "https://www.123pan.cn" },
  localStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key)
  }
};
vm.createContext(sandbox);
vm.runInContext([slice("// src/core/utils.js", "// src/api.js"), slice("// src/api.js", "// src/core/categories.js")].join("\n"), sandbox, { filename: "123-helper.user.js" });
vm.runInContext(`
globalThis.__sessionMod = { buildSessionExportPayload, parseSessionImportPayload, applySessionImportPayload };
`, sandbox, { filename: "driver.js" });
const { buildSessionExportPayload, parseSessionImportPayload, applySessionImportPayload } = sandbox.__sessionMod;

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
