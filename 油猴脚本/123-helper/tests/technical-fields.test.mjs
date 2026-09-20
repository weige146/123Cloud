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

// 5. 帧率：H.265.25fps 不能把编码版本号并进帧率（此前识别成 265.25fps）；真实小数帧率保留
fields = inferTechnicalFields("火烧红莲寺.Burning.Paradise.1994.2160p.WEB-DL.H.265.25fps.10bit.AAC.CHS.Mandarin-CSWEB");
assert.equal(fields.frameRate, "25fps");
assert.equal(fields.videoCodec, "H265");
assert.equal(fields.colorDepth, "10bit");
assert.equal(inferTechnicalFields("Movie.2020.1080p.BluRay.23.976fps.x264-GROUP").frameRate, "23.976fps");
assert.equal(inferTechnicalFields("Movie.2020.1080p.BluRay.60FPS.x264-GROUP").frameRate, "60fps");
console.log("ok H.265.25fps 帧率识别为 25fps，真实小数帧率不受影响");

// 6. REMUX：UHD.BluRay.2160p.REMUX 中间隔着分辨率段，也要识别为 UHD BluRay Remux（此前落成 UHD BluRay）
fields = inferTechnicalFields("Flight.of.the.Butterflies.2012.UHD.BluRay.2160p.REMUX.HDR.HEVC.Atmos.TrueHD.7.1-UBits");
assert.equal(fields.resourceType, "UHD BluRay Remux");
assert.equal(fields.videoFormat, "2160p");
assert.equal(fields.videoCodec, "HEVC");
assert.equal(fields.audioCodec, "TrueHD.7.1");
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

console.log("technical-fields.test.mjs 全部通过");
