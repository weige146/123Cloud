// 技术字段识别回归测试：固定映射（重点：音频编码 AV3A）+ 标题截断。
// 用法：node 油猴脚本/tests/technical-fields.test.mjs
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
  slice("// src/core/recognition-maps.js", "// src/config.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");
const driver = `;
globalThis.__recognize = { inferTechnicalFields, inferTitle, DEFAULT_FIXED_MAPPINGS };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { inferTechnicalFields, inferTitle, DEFAULT_FIXED_MAPPINGS } = sandbox.__recognize;

const sample = "See.You.Later.Maybe.S01.2026.2160p.WEB-DL.AV3A.HDR.Vivid.60FPS.H.265-GROUP";

// 1. 默认映射即可从文件名中段抽出 AV3A 音频（此前被硬编码白名单正则拦截）
let fields = inferTechnicalFields(sample);
assert.equal(fields.audioCodec, "AV3A");
assert.equal(fields.videoCodec, "H265");
assert.equal(fields.dynamicRange, "HDR.Vivid");
assert.ok(fields.effect.includes("HDR.Vivid"));
assert.equal(fields.frameRate, "60fps");
console.log("ok 默认映射识别 AV3A/H265/HDR.Vivid/60fps");

// 2. 用户在「音频编码」组手动添加的映射（别名 AV3A → 自定义写法）现在生效
fields = inferTechnicalFields(sample, [{ id: "custom-av3a", field: "audioCodec", aliases: ["AV3A"], output: "AVS3.Audio" }]);
assert.equal(fields.audioCodec, "AVS3.Audio");
console.log("ok 自定义音频编码映射生效");

// 3. 标题截断：有年份时截到年份；无年份/季标时 AV3A 不再混进标题
assert.equal(inferTitle(sample), "See You Later Maybe");
assert.equal(inferTitle("SomeShow.AV3A.H.265-GROUP"), "SomeShow");
console.log("ok 标题截断不受 AV3A 影响");

console.log("technical-fields.test.mjs 全部通过");
