// 门户域名识别回归测试。
// 背景：user.123pan.cn 是登录/账号域名，旧正则只认 www|yun 子域，脚本在登录页不启动，
// 「导入登录会话」必须先登录才能用——登录本身又新增设备，会话复用失去意义。
// 1.3.9 起白名单放行 user. 子域；登录域名不服务 /b/api，脚本 API 主机回落 www.123pan.cn。
// 用法：node 油猴脚本/123-helper/tests/portal-host.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const lines = fs.readFileSync(scriptPath, "utf8").split("\n");
const start = lines.findIndex((line) => line.includes("function isOfficialPanPortalHost"));
const end = lines.findIndex((line) => line.includes("function isOfficialPanPortalHost")) >= 0
  ? lines.findIndex((line, index) => index > start && line.startsWith("  (() => {"))
  : -1;
assert.ok(start > 0 && end > start, "无法在脚本中定位 isOfficialPanPortalHost");
const sandbox = { location: { hostname: "www.123pan.cn" } };
vm.createContext(sandbox);
vm.runInContext(lines.slice(start, end).join("\n"), sandbox, { filename: "123-helper.user.js" });
const isOfficialPanPortalHost = sandbox.isOfficialPanPortalHost;
assert.equal(typeof isOfficialPanPortalHost, "function");

// 登录/账号域名：未登录也要能跑脚本（会话导入入口）
for (const host of ["user.123pan.cn", "user.123pan.com", "user.123912.com"]) {
  assert.equal(isOfficialPanPortalHost(host), true, `${host} 应被识别为门户域名`);
}
// 既有白名单不回退
for (const host of [
  "www.123pan.cn", "yun.123pan.cn", "123pan.cn",
  "www.123pan.com", "www.123865.com", "123865.com",
  "www.123912.com", "www.123635.com", "www.123684.com"
]) {
  assert.equal(isOfficialPanPortalHost(host), true, `${host} 应保持识别`);
}
// 非门户域名不误判
for (const host of [
  "evil.com", "123pan.cn.evil.com", "fakeuser.123pan.cn",
  "share.123pan.cn", "www.share.123865.com", "", "notportal"
]) {
  assert.equal(isOfficialPanPortalHost(host), false, `${host || "(空)"} 不应被识别`);
}
console.log("ok 门户域名识别：user. 登录域放行、既有域名不回退、非门户不误判");
