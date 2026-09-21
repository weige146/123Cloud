// 当前目录 ID 解析回归测试：yun.123pan.cn 会把整条目录路径用逗号拼进 homeFilePath
// （如 homeFilePath=1%2C2%2C3），必须取最后一段，否则列目录接口报「ParentFileId 格式异常」。
// 用法：node 油猴脚本/tests/read-current-directory.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const lines = fs.readFileSync(scriptPath, "utf8").split("\n");
const start = lines.findIndex((line) => line.includes("function readCurrentDirectoryId"));
const end = lines.findIndex((line) => line.includes("function readFilePageContext"));
if (start < 0 || end <= start) throw new Error("bundle markers not found: readCurrentDirectoryId .. readFilePageContext");
const code = `${lines.slice(start, end).join("\n")}\nglobalThis.__readCurrentDirectoryId = readCurrentDirectoryId;`;

function loadReader({ url, sessionPath }) {
  const sandbox = {
    URL,
    location: new URL(url),
    sessionStorage: {
      getItem: (key) => (key === "filePath" && sessionPath !== null ? JSON.stringify(sessionPath) : null)
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: "123-helper.user.js" });
  return sandbox.__readCurrentDirectoryId;
}

const cases = [
  ["单段 homeFilePath 原样返回", { url: "https://yun.123pan.cn/?homeFilePath=3614046", sessionPath: null }, "3614046"],
  ["新版整条路径取最后一段", { url: "https://yun.123pan.cn/?homeFilePath=3614046%2C35993417%2C34112786", sessionPath: null }, "34112786"],
  ["根目录 homeFilePath=0", { url: "https://yun.123pan.cn/?homeFilePath=0", sessionPath: null }, "0"],
  ["空 homeFilePath 回退 0", { url: "https://yun.123pan.cn/?homeFilePath=", sessionPath: null }, "0"],
  ["非法 homeFilePath 回退 0", { url: "https://yun.123pan.cn/?homeFilePath=abc", sessionPath: null }, "0"],
  ["路径里夹非法段时跳过取最后合法段", { url: "https://yun.123pan.cn/?homeFilePath=3614046,abc,34112786", sessionPath: null }, "34112786"],
  ["无 URL 参数时读 sessionStorage 最后一项", { url: "https://yun.123pan.cn/", sessionPath: { homeFilePath: ["3614046", "35993417"] } }, "35993417"],
  ["sessionStorage 非数字回退 0", { url: "https://yun.123pan.cn/", sessionPath: { homeFilePath: ["abc"] } }, "0"],
  ["sessionStorage 缺失回退 0", { url: "https://yun.123pan.cn/", sessionPath: null }, "0"]
];

let passed = 0;
for (const [title, env, expected] of cases) {
  const read = loadReader(env);
  assert.equal(read(), expected, title);
  passed += 1;
  console.log(`  ok ${title}`);
}
console.log(`${passed} 个用例全部通过`);
