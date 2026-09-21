// VCB-Studio 特别篇识别回归测试（仅校准流程内生效）。
// 覆盖：SP01/NCOP/CM03/Menu/SPs 目录判定为强特别篇；复用现有 S00 匹配链；
// 全局 isSpecialEpisodeHint 不被改动；正片 [01] 不被误判为特别篇。
// 用法：node 油猴脚本/tests/vcb-calibration-special.test.mjs
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
globalThis.__vcb = {
  inferTitle, parseEpisodeHint, specialContext, isSpecialEpisodeHint, matchEpisodeCandidates,
  vcbSpecialTag, hasVcbSpecial, withVcbSpecialContext
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { parseEpisodeHint, specialContext, isSpecialEpisodeHint, matchEpisodeCandidates, vcbSpecialTag, hasVcbSpecial, withVcbSpecialContext } = sandbox.__vcb;

let passed = 0;
const test = (title, fn) => { try { fn(); passed += 1; console.log(`  ok ${title}`); } catch (error) { console.error(`  FAIL ${title}\n`, error); process.exitCode = 1; } };

// —— 标签解析 ——
test("vcbSpecialTag 解析 SP01/NCOP/CM03/Menu", () => {
  const sp = vcbSpecialTag("[VCB-Studio] Show [SP01][Ma10p_1080p].mkv", "");
  assert.equal(sp.kind, "SP");
  assert.equal(sp.number, 1);
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [NCOP][Ma10p_1080p].mkv", "").kind, "NCOP");
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [CM03].mkv", "").kind, "CM");
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [CM03].mkv", "").number, 3);
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [Menu].png", "").kind, "MENU");
});
test("vcbSpecialTag 拒绝正片与无关文本", () => {
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [01][Ma10p_1080p].mkv", ""), null);
  assert.equal(vcbSpecialTag("[VCB-Studio] Show [x265_flac].mkv", ""), null);
  assert.equal(vcbSpecialTag("[PVC]xxx.mkv", ""), null);
});
test("vcbSpecialTag 通过 SPs 目录判定特别篇", () => {
  const tag = vcbSpecialTag("[VCB-Studio] Show [01][Ma10p_1080p].mkv", "Show/SPs/[01].mkv");
  assert.equal(tag.kind, "SP");
  assert.equal(tag.folder, true);
});
test("hasVcbSpecial 判定正确", () => {
  assert.equal(hasVcbSpecial("[VCB-Studio] Show [SP01].mkv", ""), true);
  assert.equal(hasVcbSpecial("[VCB-Studio] Show [NCED].mkv", ""), true);
  assert.equal(hasVcbSpecial("[VCB-Studio] Show [01].mkv", ""), false);
  assert.equal(hasVcbSpecial("[01].mkv", "Fold/SP/[01].mkv"), true);
});

// —— 上下文覆盖 ——
test("withVcbSpecialContext 把 VCB 特别篇标为强特别篇", () => {
  const base = specialContext("[VCB-Studio] Show [SP01][Ma10p_1080p].mkv");
  assert.equal(base.strong, false, "全局 specialContext 不因 SP01 判为特别篇");
  const v = withVcbSpecialContext(base, "[VCB-Studio] Show [SP01][Ma10p_1080p].mkv", "Show/SPs/1.mkv");
  assert.equal(v.strong, true);
  assert.ok(v.strongKeywords.includes("特辑"), `strongKeywords 含特辑 token: ${v.strongKeywords}`);
});
test("withVcbSpecialContext 不改动普通正片上下文", () => {
  const normal = specialContext("[VCB-Studio] Show [01][Ma10p_1080p].mkv");
  const out = withVcbSpecialContext(normal, "[VCB-Studio] Show [01][Ma10p_1080p].mkv", "Show/[01].mkv");
  assert.equal(out, normal);
  assert.equal(out.strong, false);
});
test("全局 isSpecialEpisodeHint 未被改动", () => {
  assert.equal(isSpecialEpisodeHint("[VCB-Studio] Show [SP01][Ma10p_1080p].mkv"), false);
  assert.equal(isSpecialEpisodeHint("Show S00E00 特辑"), true);
});

// —— 校准匹配 ——
const regEpisodes = [
  { id: "e1", seasonNumber: 2, episodeNumber: 1, name: "第一集", airDate: "2023-01-20" },
  { id: "e2", seasonNumber: 2, episodeNumber: 2, name: "第二集", airDate: "2023-01-27" }
];
const file = (id, name, relativePath) => ({ id, name, relativePath });
test("校准匹配：VCB SP01 命中 S00 特别篇，正片命中正季", () => {
  const s00 = [{ id: "sp1", seasonNumber: 0, episodeNumber: 1, name: "SP", airDate: "" }];
  const files = [
    file("m1", "[VCB-Studio] Show S2 [S2E01][Ma10p_1080p][x265_flac].mkv", "Show/[S2E01].mkv"),
    file("s1", "[VCB-Studio] Show S2 [SP01][Ma10p_1080p][x265_flac].mkv", "Show/SPs/[SP01].mkv")
  ];
  const matches = matchEpisodeCandidates(files, [...regEpisodes, ...s00], 2, 2);
  assert.equal(matches.get("m1")?.seasonEpisode, "S02E01", "正片应保持 S02E01");
  assert.equal(matches.get("s1")?.seasonNumber, 0, "VCB SP01 应路由到 S00");
  assert.equal(matches.get("s1")?.seasonEpisode, "S00E01");
});
test("校准匹配：无 S00 时 VCB 特别篇保持未匹配，不落到正季", () => {
  const files = [file("s1", "[VCB-Studio] Show S2 [SP01][Ma10p_1080p].mkv", "Show/SPs/[SP01].mkv")];
  const matches = matchEpisodeCandidates(files, regEpisodes, 2, 2);
  assert.equal(matches.has("s1"), false, "不应对不上 S00 时强行落正季");
});
test("校准匹配：多个歧义 S00 特别篇时保持未匹配（保守）", () => {
  const s00 = [
    { id: "sp1", seasonNumber: 0, episodeNumber: 1, name: "SP", airDate: "" },
    { id: "sp2", seasonNumber: 0, episodeNumber: 2, name: "特辑", airDate: "" }
  ];
  const files = [file("s1", "[VCB-Studio] Show S2 [SP01][Ma10p_1080p].mkv", "Show/SPs/[SP01].mkv")];
  const matches = matchEpisodeCandidates(files, [...regEpisodes, ...s00], 2, 2);
  assert.equal(matches.has("s1"), false, "歧义时不臆断，交由后续处理");
});
test("校准匹配：正片 [01] 不会被当作特别篇", () => {
  const files = [file("m1", "[VCB-Studio] Show S2 [01][Ma10p_1080p][x265_flac].mkv", "Show/[01].mkv")];
  const matches = matchEpisodeCandidates(files, regEpisodes, 2, 2);
  assert.notEqual(matches.get("m1")?.seasonNumber ?? 1, 0, "正片不因 VCB 处理进入 S00");
});

console.log(`\n${passed} 个用例全部通过`);