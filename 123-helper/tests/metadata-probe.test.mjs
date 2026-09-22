// 「识别文件元数据」（MediaInfo 分段解析）字段归一化回归：探测要覆盖命名模板的
// 杜比视界 / HDR / 帧率 / 色深 / 音轨等字段，值形态与文件名识别保持一致。
// 用法：node 油猴脚本/tests/metadata-probe.test.mjs
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
const code = slice("// src/metadata.js", "// src/menu.js");
const driver = `;
globalThis.__metadata = {
  normalizeMediaInfo, normalizeResolution, normalizeDynamicRange, normalizeDolbyVision,
  normalizeAudioCodec, normalizeVideoCodec, normalizeFrameRate, normalizeBitDepth
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { normalizeMediaInfo, normalizeResolution, normalizeDynamicRange, normalizeDolbyVision } = sandbox.__metadata;

let passed = 0;
const test = (title, fn) => {
  fn();
  passed += 1;
  console.log(`  ok ${title}`);
};

const videoTrack = (extra = {}) => ({ "@type": "Video", Width: 3840, Height: 2160, Format: "HEVC", ...extra });
const audioTrack = (extra = {}) => ({ "@type": "Audio", Format: "E AC-3", Channels: "6 channels", ...extra });

test("杜比视界：HDR_Format_String 拼接串认出 DV，并保留 HDR10 基线", () => {
  const fields = normalizeMediaInfo({
    media: {
      track: [
        videoTrack({ HDR_Format_String: "Dolby Vision, Version 1.0, dvhe.08.06, BL+RPU / SMPTE ST 2084, HDR10 compatible" }),
        audioTrack()
      ]
    }
  });
  assert.equal(fields.dolbyVision, "DV");
  assert.equal(fields.dynamicRange, "HDR10");
  assert.equal(fields.effect, "DV HDR10");
});

test("HDR10+：ST 2094 优先于基线 PQ，effect 记 HDR10", () => {
  const fields = normalizeMediaInfo({
    media: {
      track: [
        videoTrack({ HDR_Format: "SMPTE ST 2094 App 4, HDR10+ Profile B compatible", Transfer_Characteristics: "PQ" }),
        audioTrack()
      ]
    }
  });
  assert.equal(fields.dynamicRange, "HDR10+");
  assert.equal(fields.dolbyVision, undefined);
  assert.equal(fields.effect, "HDR10");
});

test("HLG 与 SDR", () => {
  assert.equal(normalizeDynamicRange({ Transfer_Characteristics: "ARIB STD-B67" }), "HLG");
  assert.equal(normalizeDynamicRange({}), "");
  const sdr = normalizeMediaInfo({ media: { track: [videoTrack(), audioTrack()] } });
  assert.equal(sdr.dynamicRange, undefined);
  assert.equal(sdr.effect, undefined);
});

test("杜比视界 CodecID/商用名兜底", () => {
  assert.equal(normalizeDolbyVision({ CodecID: "dvh1" }), "DV");
  assert.equal(normalizeDolbyVision({ CodecID: "dvhe.05" }), "DV");
  assert.equal(normalizeDolbyVision({ Format_Commercial: "Dolby Vision" }), "DV");
  assert.equal(normalizeDolbyVision({ Format: "HEVC" }), "");
});

test("分辨率统一小写 p，隔行扫描记 i", () => {
  assert.equal(normalizeResolution(1920, 1080), "1080p");
  assert.equal(normalizeResolution(1920, 1080, "Interlaced"), "1080i");
  assert.equal(normalizeResolution(1280, 720, "Interlaced"), "720i");
  assert.equal(normalizeResolution(3840, 2160, "Progressive"), "2160p");
  assert.equal(normalizeResolution(7680, 4320), "8K");
  assert.equal(normalizeResolution(0, 0), "");
});

test("音频：声道、Atmos 与多音轨计数", () => {
  const fields = normalizeMediaInfo({
    media: {
      track: [
        videoTrack(),
        audioTrack({ Format_Commercial: "Dolby Digital Plus with Dolby Atmos" }),
        audioTrack()
      ]
    }
  });
  assert.equal(fields.audioCodec, "DDP.5.1.Atmos.2Audios");
});

test("常规 1080p HEVC AAC 探测输出", () => {
  const fields = normalizeMediaInfo({
    media: {
      track: [
        videoTrack({ Width: 1920, Height: 1080, FrameRate: "23.976 (24000/1001) fps", BitDepth: "10 bits" }),
        audioTrack({ Format: "AAC LC", Channels: "2 channels" })
      ]
    }
  });
  assert.equal(fields.videoFormat, "1080p");
  assert.equal(fields.videoCodec, "HEVC");
  assert.equal(fields.frameRate, "23.976fps");
  assert.equal(fields.colorDepth, "10bit");
  assert.equal(fields.audioCodec, "AAC.2.0");
});

console.log(`metadata-probe: ${passed} 项通过`);
