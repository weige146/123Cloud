// 内置发布组替换词回归测试：验证「PT 发布组 => 标准发布组」内置表在升级时一次性补种到
// 用户已有识别词规则的后面（同义规则去重、用户规则保持在前面先生效、删除后不复活），
// 并回归 v14 清理：早期开发版种下的带 \b 旧内置行会被清掉，误收的 SONYHD/LelveTV 两条不再出现。
// 用法：node 油猴脚本/123-helper/tests/builtin-release-group-words.test.mjs
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
  slice("// src/core/categories.js", "// src/icons.js"),
  slice("// src/core/category-yaml.js", "// src/core/empty-folders.js")
].join("\n");
const driver = `;
globalThis.__builtin = { BUILTIN_RELEASE_GROUP_WORDS, seedBuiltinReleaseGroupWords, stripLegacyBuiltinReleaseGroupWords, normalizeConfig, prepareRecognitionText };
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { BUILTIN_RELEASE_GROUP_WORDS, seedBuiltinReleaseGroupWords, stripLegacyBuiltinReleaseGroupWords, normalizeConfig, prepareRecognitionText } = sandbox.__builtin;

let passed = 0;
const test = (title, fn) => {
  fn();
  passed += 1;
  console.log(`  ok ${title}`);
};

test("内置表与高清剧集网对应表逐条对齐，纯组名写法且不含存疑条目", () => {
  const expected = [
    "GPTHD => OurTV", "SeeWEB => OurTV", "CTRLHD => WiKi", "DreamHD => CHDWEB",
    "BlackTV => CHDWEB", "ColorTV => HHWEB", "Xiaomi => HDCTV", "Huawei => PTerWEB",
    "Momoweb => PTerWEB", "DDHDTV => QHStudio", "Xunlei => QHStudio", "NukeHD => QHStudio",
    "TagWeb => LeagueWEB", "ZeroTV => ADWeb", "ZerTV => ADWeb", "MiniHD => FRDS",
    "BitsTV => cXcY@FRDS", "ALT => BeiTai", "BATWEB => HHWEB", "ParkTV => HDSWEB",
    "MarryTV => HHWEB", "ParkHD => HDSWEB", "QuickIO => ADE", "DeePTV => ADWeb"
  ];
  assert.equal(BUILTIN_RELEASE_GROUP_WORDS.length, expected.length);
  assert.deepEqual([...BUILTIN_RELEASE_GROUP_WORDS], expected);
  for (const line of BUILTIN_RELEASE_GROUP_WORDS) {
    const match = line.match(/^(\S.*) => (\S.*)$/);
    assert.ok(match, `替换词格式不对：${line}`);
    assert.ok(!line.includes("\\b"), `内置表保持纯文本写法，不带 \\b：${line}`);
    assert.doesNotThrow(() => new RegExp(match[1]), `替换前内容必须是合法正则：${line}`);
  }
});

test("新装用户：内置表原样进入识别词", () => {
  const config = normalizeConfig({});
  assert.deepEqual([...config.library.recognition.customWords], [...BUILTIN_RELEASE_GROUP_WORDS]);
  assert.equal(config.schemaVersion, 14);
});

test("老用户升级：内置表追加在已有规则后面，已有同义规则不重复", () => {
  const config = normalizeConfig({ schemaVersion: 12, library: { recognition: { customWords: ["广告", "gpthd => ourtv"] } } });
  const words = config.library.recognition.customWords;
  assert.equal(words[0], "广告");
  assert.equal(words[1], "gpthd => ourtv");
  assert.equal(words.length, 2 + BUILTIN_RELEASE_GROUP_WORDS.length - 1);
  assert.equal(words.filter((line) => /gpthd/i.test(line)).length, 1);
  assert.deepEqual([...words.slice(-1)], [[...BUILTIN_RELEASE_GROUP_WORDS].at(-1)]);
});

test("开发版残留清理：带 \\b 的旧内置行被清掉并按新表补种，用户其他规则不动", () => {
  const config = normalizeConfig({ schemaVersion: 13, library: { recognition: { customWords: [
    "广告",
    "\\bGPTHD\\b => OurTV",
    "\\bSONYHD\\b => ADWED",
    "\\bLelveTV\\b => HHWEB",
    "\\bMyGroup\\b => Foo"
  ] } } });
  const words = [...config.library.recognition.customWords];
  assert.deepEqual(words.slice(0, 2), ["广告", "\\bMyGroup\\b => Foo"]);
  assert.deepEqual(words.slice(2), [...BUILTIN_RELEASE_GROUP_WORDS]);
  assert.ok(words.every((line) => !/SONYHD|LelveTV/i.test(line)), "误收的两条不应再出现");
});

test("stripLegacyBuiltinReleaseGroupWords 只清理旧内置键，不碰用户自写的 \\b 规则", () => {
  const kept = stripLegacyBuiltinReleaseGroupWords(["\\bMyGroup\\b => Foo", "\\bMiniHD\\b => FRDS", "广告"]);
  assert.deepEqual([...kept], ["\\bMyGroup\\b => Foo", "广告"]);
  assert.deepEqual([...stripLegacyBuiltinReleaseGroupWords("广告")], []);
});

test("已升级用户（v14）：删掉内置条目后补种不复活", () => {
  const config = normalizeConfig({ schemaVersion: 14, library: { recognition: { customWords: ["广告"] } } });
  assert.deepEqual([...config.library.recognition.customWords], ["广告"]);
});

test("补种幂等：重复归一化结果不变", () => {
  const once = normalizeConfig({ schemaVersion: 12, library: { recognition: { customWords: ["广告"] } } });
  const twice = normalizeConfig(once);
  assert.deepEqual(JSON.parse(JSON.stringify(twice)), JSON.parse(JSON.stringify(once)));
});

test("seedBuiltinReleaseGroupWords 对非法入参容错", () => {
  assert.deepEqual([...seedBuiltinReleaseGroupWords(null)], [...BUILTIN_RELEASE_GROUP_WORDS]);
  assert.deepEqual([...seedBuiltinReleaseGroupWords("广告")], [...BUILTIN_RELEASE_GROUP_WORDS]);
});

test("真实替换：PT 组名换成标准组名，用户已有规则排在前面先生效", () => {
  const words = normalizeConfig({}).library.recognition.customWords;
  const hit = prepareRecognitionText("A.Quarter.S01.2024.1080p.WEB-DL.AAC.H264-GPTHD", words);
  assert.ok(hit.text.includes("OurTV"), hit.text);
  assert.ok(!hit.text.includes("GPTHD"), hit.text);
  const alt = prepareRecognitionText("Boy.S01.ALT.1080p-HDTV", words);
  assert.ok(alt.text.includes("BeiTai"), alt.text);
  const config = normalizeConfig({ schemaVersion: 12, library: { recognition: { customWords: ["Xiaomi => MIJIA"] } } });
  const result = prepareRecognitionText("Xiaomi.Documentary.2024.1080p", [...config.library.recognition.customWords]);
  assert.ok(result.text.includes("MIJIA"), result.text);
  assert.ok(!result.text.includes("HDCTV"), result.text);
});

console.log(`\n共 ${passed} 个用例，全部通过`);
