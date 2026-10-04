// 发布组识别回归测试：防止技术字段尾段被误判成发布组。
// 背景：尾部候选（「…AAC-SDR」的 SDR、「…HEVC HDR10」的 HDR10、「…Directors Cut」的 Cut）
// 长得像组名就会被连字符/通用分支吃掉，产出 `….HEVC-HDR10` 这种
// 同一个词在中段和尾部各出现一次的文件名。判定入口是 knownMediaTerm。
// 用法：node 油猴脚本/tests/release-group-known-term.test.mjs
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
  slice("// src/core/release-group.js", "// src/core/rename.js")
].join("\n");
const driver = `
globalThis.__group = { parseReleaseGroup, knownMediaTerm };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { parseReleaseGroup, knownMediaTerm } = sandbox.__group;

// 1. 动态范围词尾不再被当成组名（漏词表会让尾部动态范围直接变成发布组名）
for (const term of ["SDR", "HDR", "HDR10", "HDRVIVID", "VIVID", "HLG", "DV", "DVI", "DOLBYVISION"]) {
  assert.ok(knownMediaTerm(term), `${term} 应是已知媒体词，不该被当组名`);
}
assert.ok(knownMediaTerm("Vivid"), "HDR Vivid 的尾段是 Vivid，必须单列 —— 去分隔符后是 VIVID，不匹配 HDRVIVID");
console.log("ok 动态范围词都在已知媒体词表里（含单列的 VIVID）");

// 2. DV 的 profile 记号：DV.P5 / DoVi P8 的 P5、P8 只是 DV 的写法，不是组名
for (const term of ["DVP5", "DVP8", "DOP5", "DOP8", "DVIP8"]) {
  assert.ok(knownMediaTerm(term), `${term} 应是已知媒体词（DV profile 写法），不该被当组名`);
}
console.log("ok DV profile 写法（P5/P8）都是已知媒体词");

// 3. 纯声道数必须能用**原文**判定：upper 已剥掉点号，7.1 在 upper 里是 71
assert.ok(knownMediaTerm("7.1"), "7.1 应是已知媒体词");
assert.ok(knownMediaTerm("5.1ch"), "5.1ch 应是已知媒体词");
assert.ok(knownMediaTerm("6ch"), "6ch 应是已知媒体词");
console.log("ok 纯声道数（7.1 / 5.1ch / 6ch）是已知媒体词");

// 4. 多词版本标记被切开的后半截不该是组名
for (const term of ["CUT", "VERSION", "EDITION", "COLLECTION", "REMASTER", "REGRADE", "RETAUCH", "RECOLOR"]) {
  assert.ok(knownMediaTerm(term), `${term} 应是已知媒体词，不该被当组名`);
}
console.log("ok 版本词尾段都在已知媒体词表里");

// 5. 端到端：真实文件名不应产出发布组
const notGroups = [
  "Movie.2024.1080p.WEB-DL.HEVC.AAC.SDR.mkv",
  "Movie.2024.1080p.WEB-DL.SDR.HEVC.AAC.mkv",
  "Show.S01E01.2160p.WEB-DL.HEVC.HDR10.mkv",
  "Show.S01E01.2160p.WEB-DL.DV.HDR10.HEVC.mkv",
  "Show.S01E01.2160p.WEB-DL.DV.P8.HEVC.mkv",
  "Show.S01E01.2160p.WEB-DL.DV.P5.HDR10.HEVC.mkv",
  "Show.S01E01.2160p.WEB-DL.DoVi.P8.HDR10.HEVC.mkv",
  "Show.S01E01.1080p.WEB-DL.DTS-HD.MA.7.1.mkv",
  "Show.S01E01.1080p.WEB-DL.HEVC.6ch.mkv",
  "Movie.2024.1080p.BluRay.2160p.HEVC.Directors.Cut.mkv"
];
for (const name of notGroups) {
  const hit = parseReleaseGroup(name);
  assert.equal(hit, null, `不应识别出发布组: ${name} → ${JSON.stringify(hit)}`);
}
console.log(`ok ${notGroups.length} 个技术字段尾段文件名都不产出发布组`);

// 6. 正向对照：真组名必须仍然认得出来，不能被词表扩大误伤
const realGroups = [
  ["Show.S01E01.2160p.WEB-DL.HEVC-CtrlHD", "CtrlHD"],
  ["Show.S01E01.1080p.WEB-DL.H264-NTb", "NTb"],
  ["Movie.2024.2160p.UHD.BluRay.REMUX.HEVC-FraMeSToR", "FraMeSToR"],
  ["Movie.2024.1080p.WEB-DL.HDR10.HEVC.CMCT", "CMCT"]
];
for (const [name, group] of realGroups) {
  const hit = parseReleaseGroup(name);
  assert.ok(hit, `应识别出发布组: ${name}`);
  assert.equal(hit.group, group, `组名不对: ${name} → ${hit.group}`);
}
console.log(`ok ${realGroups.length} 个真组名未被误伤`);

console.log("release-group-known-term.test.mjs 全部通过");