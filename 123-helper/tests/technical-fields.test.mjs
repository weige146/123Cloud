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

// 4. 全新编码（不在默认白名单，如虚构的 XXEA）：未配置别名时提不出来；
//    在「音频编码」组加别名后，提取、声道后缀、标题截断全部生效，无需改代码
const customCodecMappings = [{ id: "custom-xxea", field: "audioCodec", aliases: ["XXEA"], output: "XXEA" }];
assert.equal(inferTechnicalFields("Movie.2024.1080p.XXEA.2.0.H.265-GROUP").audioCodec, "");
fields = inferTechnicalFields("Movie.2024.1080p.XXEA.2.0.H.265-GROUP", customCodecMappings);
assert.equal(fields.audioCodec, "XXEA.2.0");
console.log("ok 全新编码加别名后可提取（含声道后缀），未配置时不误提取");

fields = inferTechnicalFields("Show.S01E01.XXEA.H.265-GROUP", customCodecMappings);
assert.equal(fields.audioCodec, "XXEA");
assert.equal(inferTitle("Movie.XXEA.2.0.H.265-GROUP", customCodecMappings), "Movie");
console.log("ok 全新编码无声道写法识别 + 标题截断生效");

// 5. 帧率：H.265.25fps 不能把编码版本号并进帧率（此前识别成 265.25fps）；
//    小数帧率按 PT 惯例取整成常用档（与 MediaInfo 探测同一口径）
fields = inferTechnicalFields("火烧红莲寺.Burning.Paradise.1994.2160p.WEB-DL.H.265.25fps.10bit.AAC.CHS.Mandarin-CSWEB");
assert.equal(fields.frameRate, "25fps");
assert.equal(fields.videoCodec, "H265");
assert.equal(fields.colorDepth, "10bit");
assert.equal(inferTechnicalFields("Movie.2020.1080p.BluRay.23.976fps.x264-GROUP").frameRate, "24fps",
  "23.976 按 PT 惯例进位成 24fps");
assert.equal(inferTechnicalFields("Movie.2020.1080p.BluRay.60FPS.x264-GROUP").frameRate, "60fps");
console.log("ok H.265.25fps 帧率识别为 25fps，23.976 进位成 24fps");

// 5b. 帧率取整与冷门档过滤：29.97/59.94/119.88 各自进位，48/100 这类不标。
//     与 metadata-probe.test.mjs 的 normalizeFrameRate 断言是同一套口径。
for (const [name, expected] of [
  ["Show.S01.29.97fps.1080p.WEB-DL.HEVC-GROUP", "30fps"],
  ["Show.S01.59.94fps.1080p.WEB-DL.HEVC-GROUP", "60fps"],
  ["Show.S01.119.88fps.1080p.WEB-DL.HEVC-GROUP", "120fps"],
  ["Show.S01.24fps.1080p.WEB-DL.HEVC-GROUP", "24fps"],
  ["Show.S01.25fps.1080p.WEB-DL.HEVC-GROUP", "25fps"],
  ["Show.S01.50fps.1080p.WEB-DL.HEVC-GROUP", "50fps"],
  ["Show.S01.48fps.1080p.WEB-DL.HEVC-GROUP", ""],
  ["Show.S01.100fps.1080p.WEB-DL.HEVC-GROUP", ""]
]) {
  assert.equal(inferTechnicalFields(name).frameRate, expected, `帧率取整/过滤: ${name}`);
}
console.log("ok 帧率取整成 PT 常用档，48fps/100fps 不写");

// 5c. 8bit 是默认规格，PT 命名不标；10bit/12bit 照常识别
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.8bit.HEVC-GROUP").colorDepth, "",
  "8bit 是默认规格，不该写进名字");
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.10bit.HEVC-GROUP").colorDepth, "10bit");
assert.equal(inferTechnicalFields("Movie.2021.2160p.BluRay.12bit.HEVC-GROUP").colorDepth, "12bit");
console.log("ok 8bit 不再标注，10bit/12bit 不受影响");

// 6. REMUX：UHD.BluRay.2160p.REMUX 中间隔着分辨率段，也要识别为 UHD BluRay Remux（此前落成 UHD BluRay）
fields = inferTechnicalFields("Flight.of.the.Butterflies.2012.UHD.BluRay.2160p.REMUX.HDR.HEVC.Atmos.TrueHD.7.1-UBits");
assert.equal(fields.resourceType, "UHD BluRay Remux");
assert.equal(fields.videoFormat, "2160p");
assert.equal(fields.videoCodec, "HEVC");
assert.equal(fields.audioCodec, "TrueHD.7.1.Atmos");
assert.equal(inferTechnicalFields("Movie.2018.1080p.UHD.BluRay.REMUX.HEVC-GROUP").resourceType, "UHD BluRay Remux");
console.log("ok REMUX 与 UHD BluRay 隔段出现时识别为 UHD BluRay Remux");

// 6b. REMUX：BluRay.1080p.Remux（分辨率插在 BluRay 和 Remux 中间）此前落到单独 Remux 档；
//     BD.Remux 隔段同理；纯 Remux（无 BluRay 记号）保持 Remux 不误升
assert.equal(inferTechnicalFields("The.Movie.2019.BluRay.1080p.Remux.AVC.FLAC.2.0-GROUP").resourceType, "BluRay Remux");
assert.equal(inferTechnicalFields("The.Movie.2019.1080p.BluRay.Remux.AVC.FLAC.2.0-GROUP").resourceType, "BluRay Remux");
assert.equal(inferTechnicalFields("The.Movie.2019.BD.1080p.Remux.AVC-GROUP").resourceType, "BluRay Remux");
assert.equal(inferTechnicalFields("The.Movie.2019.UHD.BluRay.2160p.Remux.HEVC-GROUP").resourceType, "UHD BluRay Remux");
assert.equal(inferTechnicalFields("The.Movie.2019.1080p.Remux.H.264-GROUP").resourceType, "Remux");
console.log("ok BluRay.1080p.Remux 分辨率隔段识别为 BluRay Remux，纯 Remux 不误升");

// 7. 国产 4K 广播录制：AVS2 编码、UHDTV 资源类型、DD2.0 是 Dolby Digital 不是 DDP
//    （此前 DD+ 别名归一化剥掉 + 退化成 DD、表序又在 DD 之前，DD2.0 被前缀误判成 DDP）
fields = inferTechnicalFields("[四川卫视4K超高清频道 故乡几万里].SCTV-4K.My.Hometown.Across.The.Ocean.2024.S01.2160p.50fps.UHDTV.AVS2.10bit.HLG.DD2.0-QHstudIo");
assert.equal(fields.resourceType, "UHDTV");
assert.equal(fields.videoCodec, "AVS2");
assert.equal(fields.audioCodec, "DD.2.0");
assert.equal(fields.dynamicRange, "HLG");
assert.equal(fields.frameRate, "50fps");
assert.equal(fields.colorDepth, "10bit");
assert.equal(inferTechnicalFields("Show.2024.2160p.UHDTV.AVS3.HLG.DD5.1-GROUP").videoCodec, "AVS3");
assert.equal(inferTechnicalFields("Show.2024.2160p.UHDTV.AVS+.HLG.DD2.0-GROUP").videoCodec, "AVS+");
assert.equal(inferTechnicalFields("Movie.2024.1080p.WEB-DL.DD+5.1.H.264-GROUP").audioCodec, "DDP.5.1");
assert.equal(inferTechnicalFields("Movie.2024.1080p.WEB-DL.DDP5.1.Atmos.H.265-GROUP").audioCodec, "DDP.5.1.Atmos");
assert.equal(inferTechnicalFields("Show.S01.2160p.IPTV.H.265.DD.2.0-GROUP").audioCodec, "DD.2.0");
console.log("ok AVS2/AVS3/AVS+ 编码、UHDTV 资源类型、DD2.0 不再误判成 DDP");

// 8. Atmos 写在编码前面（HDH 式命名）：只补 Atmos 判定，编码与声道照常识别；
//    对照组：Atmos 在后/在编码中间的既有写法不受影响；单独 Atmos 不触发音频识别
fields = inferTechnicalFields("Superman.1978.2160p.REISSUE.UHD.Blu-ray.REMUX.HEVC.Atmos.TrueHD7.1-HDH");
assert.equal(fields.audioCodec, "TrueHD.7.1.Atmos");
assert.equal(fields.videoCodec, "HEVC");
assert.equal(fields.resourceType, "UHD BluRay Remux");
assert.equal(inferTechnicalFields("Show.2023.1080p.Atmos.DDP5.1.H.264-GROUP").audioCodec, "DDP.5.1.Atmos");
assert.equal(inferTechnicalFields("Show.2023.1080p.Dolby.Atmos.TrueHD.7.1-GROUP").audioCodec, "TrueHD.7.1.Atmos");
assert.equal(inferTechnicalFields("Movie.2024.1080p.TrueHD7.1.Atmos-GROUP").audioCodec, "TrueHD.7.1.Atmos");
assert.equal(inferTechnicalFields("Movie.2024.1080p.TrueHD.Atmos.7.1-GROUP").audioCodec, "TrueHD.7.1.Atmos");
assert.equal(inferTechnicalFields("Movie.2024.1080p.Atmos.H.265-GROUP").audioCodec, "");
console.log("ok Atmos 写在编码前面也识别，单独 Atmos 不触发音频编码");

// 8b. REMUX：连字符写法 UHD.Blu-ray.REMUX 此前落到 Blu Ray Remux 条目后不再补升；
//     无 UHD 记号的普通蓝光连字符 Remux 保持 BluRay Remux
assert.equal(inferTechnicalFields("Movie.2010.UHD.Blu-ray.REMUX.2160p.HEVC-GROUP").resourceType, "UHD BluRay Remux");
assert.equal(inferTechnicalFields("Movie.2010.Blu-ray.REMUX.1080p.AVC-GROUP").resourceType, "BluRay Remux");
console.log("ok 连字符 UHD.Blu-ray.REMUX 补升为 UHD BluRay Remux");

// 9. SDR 一律不标注：文件名里的 SDR 不写进整理后的名字（动态范围与效果都为空）。
//    HDR10 / HLG / HDR10+ / HDR.Vivid 等其他档位不受影响。
for (const name of [
  "Movie.2024.1080p.WEB-DL.SDR.HEVC.AAC",
  "Movie.2024.1080p.WEB-DL.HEVC.AAC.SDR",
  "Movie.2024.2160p.UHD.BluRay.SDR.HEVC.TrueHD",
  "Movie.2024.1080p.WEB-DL.SDR"
]) {
  fields = inferTechnicalFields(name);
  assert.ok(!fields.dynamicRange, `SDR 不应成为动态范围: ${name} → ${fields.dynamicRange}`);
  assert.ok(!fields.effect, `SDR 不应进 effect: ${name} → ${fields.effect}`);
}
// 与其他档位共现时取真档位，SDR 不参与抢占也不产生额外标记
fields = inferTechnicalFields("Movie.2024.1080p.WEB-DL.HDR10.HEVC.AAC.SDR");
assert.equal(fields.dynamicRange, "HDR10");
assert.equal(fields.effect, "HDR10", `SDR 不该混进 effect: ${fields.effect}`);
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR10.HEVC.AAC").dynamicRange, "HDR10");
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HLG.HEVC.AAC").dynamicRange, "HLG");
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR10+.HEVC.AAC").dynamicRange, "HDR10+");
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.HDR.Vivid.HEVC.AAC").dynamicRange, "HDR.Vivid");
assert.equal(DEFAULT_FIXED_MAPPINGS.filter((e) => e.field === "dynamicRange" && e.output === "SDR").length, 0,
  "SDR 映射条目应已移除");
console.log("ok SDR 不再标注，其他动态范围档位不受影响");

// 10. DV 的 profile 记号（DV.P5 / DV.P8 / DoVi P8）只是 DV 的写法，不单独标注 profile，
//     也不影响 DV 与动态范围的识别结果
for (const name of [
  "Movie.2024.2160p.WEB-DL.DV.P5.HDR10.HEVC.DDP5.1",
  "Movie.2024.2160p.WEB-DL.DV.P8.HDR10.HEVC.DDP5.1",
  "Movie.2024.2160p.WEB-DL.DoVi.P5.HDR10.HEVC.DDP5.1",
  "Movie.2024.2160p.WEB-DL.DoVi.P8.HDR10.HEVC.DDP5.1",
  "Movie.2024.2160p.WEB-DL.DV.P8.HEVC.DDP5.1"
]) {
  fields = inferTechnicalFields(name);
  assert.equal(fields.dolbyVision, "DV", `DV profile 写法应归一为 DV: ${name}`);
  assert.ok(!/\bP[578]\b/.test(fields.effect), `profile 不应单独出现在 effect: ${name} → ${fields.effect}`);
}
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.DV.P8.HDR10.HEVC.DDP5.1").effect, "DV HDR10");
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.DV.HDR10.HEVC.DDP5.1").effect, "DV HDR10",
  "带不带 profile 写法的 effect 口径应一致");
assert.equal(inferTechnicalFields("Movie.2024.2160p.WEB-DL.DV.P8.HEVC.DDP5.1").dynamicRange, "",
  "只有 DV 没有动态范围时不应凭空产出动态范围");
console.log("ok DV.P5 / DV.P8 / DoVi.P8 归一为 DV，profile 不单独标注");

// 11. 编码名以数字结尾时，声道不能被编码尾巴带偏：EAC3.5.1 的编码尾巴「3」与声道
//     首位「5」会拼成假声道「3.5」。声道必须锚在 token 末尾（Atmos/JOC 之前）。
for (const [name, expected] of [
  ["Movie.2021.1080p.BluRay.EAC3.5.1.HEVC-GROUP", "DDP.5.1"],
  ["Movie.2021.1080p.BluRay.AC3.2.0.HEVC-GROUP", "DD.2.0"],
  ["Movie.2021.1080p.BluRay.AV3A.5.1.HEVC-GROUP", "AV3A.5.1"],
  ["Movie.2021.1080p.BluRay.DD5.1.HEVC-GROUP", "DD.5.1"],
  ["Movie.2021.1080p.BluRay.EAC3.5.1.Atmos.HEVC-GROUP", "DDP.5.1.Atmos"],
  ["Movie.2021.1080p.BluRay.TrueHD.7.1.Atmos.HEVC-GROUP", "TrueHD.7.1.Atmos"]
]) {
  assert.equal(inferTechnicalFields(name).audioCodec, expected, `声道应锚在末尾: ${name}`);
}
console.log("ok 数字结尾的编码名（EAC3/AC3/AV3A/DD）不再带偏声道");

// 12. 高规格标记归 highQuality，不混进动态范围；DV/HDR 才是动态范围。
//     口径参照开源整理工具（MoviePilot 的 effect/动态范围分工）与客户端 effect 规则表。
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.HQ.HEVC-GROUP").highQuality, "HQ");
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.EDR.HEVC-GROUP").highQuality, "EDR",
  "EDR 是高规格标记，不该进 dynamicRange");
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.MAXPLUS.HEVC-GROUP").highQuality, "MAXPLUS",
  "MAXPLUS 是高规格标记，不该进 originalEdition 版本");
assert.equal(inferTechnicalFields("Doc.2020.2160p.HDTV.EDR.HDR10.HEVC-GROUP").dynamicRange, "HDR10",
  "EDR 挪走后 dynamicRange 只留 HDR10");
assert.equal(inferTechnicalFields("Movie.2021.1080p.BluRay.MAXPLUS.HEVC-GROUP").originalEdition, "",
  "MAXPLUS 不再算地区版/版本");
console.log("ok HQ/EDR/MAXPLUS 归 highQuality，DV/HDR 归动态范围");

// 13. 高规格标记可共现：单值取表序第一条会漏，按出现位置聚合成多值
const multi = inferTechnicalFields("Movie.2021.1080p.BluRay.HQ.MAXPLUS.EDR-GROUP");
assert.equal(multi.highQuality, "HQ MAXPLUS EDR", "三个高规格标记都要保留，且按出现顺序");
assert.equal(multi.effect, "HQ MAXPLUS EDR", "effect 汇总也要带上全部高规格标记");
assert.equal(inferTechnicalFields("Show.S01.2160p.WEB-DL.DV.HDR10.HQ-GROUP").effect, "DV HDR10 HQ",
  "DV + 动态范围 + 高规格按固定顺序汇总");
console.log("ok 高规格标记共现时聚合成多值，不再漏");

// 14. 补齐的常见技术名（只补经甄别不会误伤真实片名的）
for (const [name, key, expected] of [
  ["Show.S01.1080p.PDTV.HEVC-GROUP", "resourceType", "HDTV"],
  ["Movie.2024.1080p.WEBMux.AAC-GROUP", "resourceType", "WEBMux"],
  ["Movie.2020.1080p.BRRip.HEVC-GROUP", "resourceType", "BDRip"],
  ["Movie.2024.2160p.BluRay.ProRes-GROUP", "videoCodec", "ProRes"],
  ["Movie.2021.1080p.BluRay.MLP.5.1-GROUP", "audioCodec", "TrueHD.5.1"],
  ["Show.S01.2160p.WEB-DL.sHDR.HEVC-GROUP", "dynamicRange", "HDR"],
  ["Movie.2024.1080p.BluRay.2560p.HEVC-GROUP", "videoFormat", "1440p"],
  ["Movie.2024.1080p.BluRay.HKG.HEVC-GROUP", "originalEdition", "HK"],
  ["Movie.2024.1080p.BluRay.SGP.HEVC-GROUP", "originalEdition", "SGP"],
  ["Film.2019.1080p.BluRay.Final.Cut.HEVC-GROUP", "originalEdition", "Final Cut"],
  ["Film.2019.1080p.BluRay.Limited.Edition.HEVC-GROUP", "originalEdition", "Limited Edition"]
]) {
  assert.equal(inferTechnicalFields(name)[key], expected, `${key}: ${name}`);
}
console.log("ok 补齐的常见技术名生效");

// 15. 高危短词不得进表：会误伤真实片名（My.Future.Hero / Uncut.Gems / The.Complete）
for (const [name, key] of [
  ["My.Future.Hero.2024.1080p.WEB-DL.HEVC-GROUP", "originalEdition"],
  ["Uncut.Gems.S01E01.1080p.WEB-DL.HEVC-GROUP", "originalEdition"],
  ["The.Complete.2024.1080p.BluRay.HEVC-GROUP", "originalEdition"],
  ["My.Future.Hero.2024.1080p.WEB-DL.HEVC-GROUP", "highQuality"]
]) {
  assert.equal(inferTechnicalFields(name)[key], "", `不该把片名里的词认成技术字段: ${name} → ${key}`);
}
console.log("ok MY/Uncut/Complete 这类会误伤片名的词没有进表");

console.log("technical-fields.test.mjs 全部通过");
