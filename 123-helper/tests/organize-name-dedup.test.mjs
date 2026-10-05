// 整理命名去重回归测试：{effect} 汇总字段（DV/HDR/HQ/3D）与模板里单独的
// {dolbyVision}/{dynamicRange}/{highQuality} 同时存在时不能重复输出。
// 重点回归：dynamicRange 的 HDR.Vivid 带点号，在 effect 里是**一个** token，
// 只按空格/斜杠切分；此前 represented 只逐词展开（HDR / VIVID），整串 HDRVIVID
// 匹配不上，模板会把 HDR.Vivid 输出两遍（整理预览里表现为 HDR.Vivid.HDR.Vivid）。
// 用法：node 油猴脚本/tests/organize-name-dedup.test.mjs
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
  slice("// src/core/categories.js", "// src/core/recognition-maps.js"),
  slice("// src/core/recognition-maps.js", "// src/config.js"),
  slice("// src/config.js", "// src/icons.js"),
  slice("// src/core/recognition-words.js", "// src/core/recognition.js"),
  slice("// src/core/recognition.js", "// src/core/template.js"),
  slice("// src/core/template.js", "// src/core/organize.js"),
  slice("// src/core/organize.js", "// src/core/organize-strategy.js")
].join("\n");
const driver = `;
globalThis.__dedup = { buildFileName, buildSeasonFolder, filenameTemplateValues, inferTechnicalFields, DEFAULT_CONFIG, field, separator };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { buildFileName, buildSeasonFolder, filenameTemplateValues, inferTechnicalFields, DEFAULT_CONFIG, field, separator } = sandbox.__dedup;

const base = { title: "The Old Story of Yu Hong", year: "2026", videoFormat: "2160p", resourceType: "WEB-DL", releaseGroup: "UBWEB", colorDepth: "10bit", videoCodec: "H265", audioCodec: "DDP.5.1" };
const tvName = (fields) => buildFileName({ ...base, mediaType: "tv", seasonEpisode: "S01E01", ...fields }, ".mkv", DEFAULT_CONFIG.templates);
const count = (text, token) => text.split(token).length - 1;

// 1. 截图里的真实场景：HDR.Vivid 只能出现一次
let name = tvName(inferTechnicalFields("The.Old.Story.of.Yu.Hong.2026.S01E01.2160p.WEB-DL.HDR.Vivid.10bit.H265.DDP5.1-UBWEB"));
assert.equal(count(name, "HDR.Vivid"), 1, `HDR.Vivid 不该重复: ${name}`);
assert.ok(name.includes("WEB-DL.HDR.Vivid.10bit"), `字段顺序应保持: ${name}`);
console.log("ok HDR.Vivid 只输出一次");

// 2. HDRVivid 无点号写法同样归一成 HDR.Vivid，不重复
name = tvName(inferTechnicalFields("Show.S01E01.2160p.WEB-DL.HDRVivid.10bit.H265.DDP5.1-GROUP"));
assert.equal(count(name, "HDR.Vivid"), 1, `HDRVivid 写法不该重复: ${name}`);
console.log("ok HDRVivid 无点号写法也归一且不重复");

// 3. DV + HDR.Vivid：DV 与 HDR.Vivid 各一次
name = tvName(inferTechnicalFields("Movie.2024.2160p.WEB-DL.DV.HDR.Vivid.HEVC.DDP5.1-GROUP"));
assert.equal(count(name, "DV"), 1, `DV 不该重复: ${name}`);
assert.equal(count(name, "HDR.Vivid"), 1, `DV+HDR.Vivid 不该重复: ${name}`);
console.log("ok DV + HDR.Vivid 各输出一次");

// 4. HQ + HDR.Vivid：高规格与动态范围都不重复
name = tvName(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR.Vivid.HQ.HEVC-GROUP"));
assert.equal(count(name, "HDR.Vivid"), 1, `HQ+HDR.Vivid 不该重复: ${name}`);
assert.equal(count(name, "HQ"), 1, `HQ 不该重复: ${name}`);
console.log("ok HQ + HDR.Vivid 都不重复");

// 5. 对照组：其它档位维持原状（HDR10 / HDR10+ / HLG / 多值高规格）
assert.equal(tvName(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR10.HEVC-GROUP")).includes("HDR10.HEVC"), true);
assert.equal(count(tvName(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR10+.HEVC-GROUP")), "HDR10+"), 1);
assert.equal(count(tvName(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HLG.HEVC-GROUP")), "HLG"), 1);
const multi = tvName(inferTechnicalFields("Movie.2021.1080p.BluRay.HQ.MAXPLUS.EDR-GROUP"));
assert.equal(count(multi, "HQ"), 1);
assert.equal(count(multi, "MAXPLUS"), 1);
assert.equal(count(multi, "EDR"), 1);
console.log("ok HDR10/HDR10+/HLG 与 HQ MAXPLUS EDR 多值不受影响");

// 6. 原地整理季目录（inPlaceSeasonFolder）同样不能重复
const vivid = { ...inferTechnicalFields("Show.S01.2160p.WEB-DL.HDR.Vivid.HEVC-GROUP"), mediaType: "tv", title: "Show", season: "1" };
const folder = buildSeasonFolder(vivid, DEFAULT_CONFIG.templates.inPlaceSeasonFolder);
assert.equal(count(folder, "HDR.Vivid"), 1, `原地季目录不该重复: ${folder}`);
console.log("ok 原地整理季目录不重复");

// 7. 模板感知：只留 {effect} 时，DV/HDR/高规格必须原样保留（此前会被整段丢掉）
const onlyEffectTv = [field("title"), separator("."), field("year"), separator("."), field("effect")];
const onlyEffectFields = { ...base, mediaType: "tv", title: "Movie", year: "2024", dolbyVision: "DV", dynamicRange: "HDR10", highQuality: "HQ", effect: "DV HDR10 HQ" };
name = buildFileName(onlyEffectFields, ".mkv", { tv: onlyEffectTv, movie: onlyEffectTv });
assert.equal(name, "Movie.2024.DV.HDR10.HQ.mkv", `只留 {effect} 时应保留汇总: ${name}`);
console.log("ok 模板只留 {effect} 时 DV/HDR/高规格不丢");

// 8. 模板含 {effect} + {dynamicRange}（无 {dolbyVision}）：只去掉 HDR，DV 仍由 effect 输出
let values = filenameTemplateValues(
  { ...base, dolbyVision: "DV", dynamicRange: "HDR10", effect: "DV HDR10" },
  [field("effect"), separator("."), field("dynamicRange")]
);
assert.equal(values.effect, "DV", `模板含 {dynamicRange} 时只去 HDR、保留 DV: ${values.effect}`);

// 9. 模板含 {effect} + {dynamicRange}：HDR.Vivid 整串去重
values = filenameTemplateValues(
  { ...base, dynamicRange: "HDR.Vivid", effect: "HDR.Vivid" },
  [field("effect"), separator("."), field("dynamicRange")]
);
assert.equal(values.effect, "", `模板已含 {dynamicRange} 时 effect 不应重复输出 HDR.Vivid: ${values.effect}`);
console.log("ok effect 汇总去重按模板里实际存在的字段决定");

console.log("organize-name-dedup.test.mjs 全部通过");