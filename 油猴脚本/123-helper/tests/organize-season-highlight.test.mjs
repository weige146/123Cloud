// 整理预览季集高亮回归测试：识别命中的季集编号要能在原文件名里按原文片段高亮，
// 新文件名/目标路径按最终 SxxEyy 值高亮，其余文本必须照常转义。
// 用法：node 油猴脚本/123-helper/tests/organize-season-highlight.test.mjs
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
globalThis.__highlight = {
  parseSeasonEpisode,
  seasonTokenRange,
  markSeasonEpisodeHtml,
  markSeasonEpisodeValueHtml
};
`;
const sandbox = { console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error, DOMException, structuredClone };
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { parseSeasonEpisode, seasonTokenRange, markSeasonEpisodeHtml, markSeasonEpisodeValueHtml } = sandbox.__highlight;

let passed = 0;
let chain = Promise.resolve();
const test = (title, fn) => {
  chain = chain.then(async () => {
    try {
      await fn();
      passed += 1;
      console.log(`  \u001b[32m✓\u001b[0m ${title}`);
    } catch (error) {
      console.error(`  \u001b[31m✗\u001b[0m ${title}`);
      throw error;
    }
  });
};

const sliceOf = (text2, range) => (range ? text2.slice(range.start, range.end) : null);

test("seasonTokenRange：SxxEyy 与多集合并按原文片段命中", () => {
  assert.equal(sliceOf("测试剧 S01E02.mkv", seasonTokenRange("测试剧 S01E02.mkv")), "S01E02");
  assert.equal(sliceOf("剧名.2022.S01E01-E02.1080p.mkv", seasonTokenRange("剧名.2022.S01E01-E02.1080p.mkv")), "S01E01-E02");
  // S 与 E 之间的分隔符属于命中文本，一并高亮
  assert.equal(sliceOf("Show S01. E03 1080p.mkv", seasonTokenRange("Show S01. E03 1080p.mkv")), "S01. E03");
});

test("seasonTokenRange：1x08 / EP03 / 第12集 等变体掐掉边界符", () => {
  assert.equal(sliceOf("美剧 1x08 1080p.mkv", seasonTokenRange("美剧 1x08 1080p.mkv")), "1x08");
  assert.equal(sliceOf("美剧.EP03.mkv", seasonTokenRange("美剧.EP03.mkv")), "EP03");
  assert.equal(sliceOf("古装剧 第12集.mkv", seasonTokenRange("古装剧 第12集.mkv")), "第12集");
  assert.equal(sliceOf("古装剧 第3季第12集.mkv", seasonTokenRange("古装剧 第3季第12集.mkv")), "第3季第12集");
  assert.equal(sliceOf("古装剧 第七集到第九集.mkv", seasonTokenRange("古装剧 第七集到第九集.mkv")), "第七集到第九集");
});

test("seasonTokenRange：没有季集编号时不返回区间", () => {
  assert.equal(seasonTokenRange("Some.Movie.2023.1080p.mkv"), null);
  // 只有季没有集（Season 1）不算季集编号，不做高亮
  assert.equal(seasonTokenRange("Some Show Season 1.mkv"), null);
});

test("parseSeasonEpisode：所有分支都带 tokenStart/tokenLength", () => {
  const parsed = parseSeasonEpisode("剧名 [第2季] 第05集.mkv", 1);
  assert.equal(parsed.seasonEpisode, "S02E05");
  assert.equal("剧名 [第2季] 第05集.mkv".slice(parsed.tokenStart, parsed.tokenStart + parsed.tokenLength), "第2季] 第05集");
});

test("markSeasonEpisodeHtml：片段包 mark，其余文本转义", () => {
  assert.equal(
    markSeasonEpisodeHtml("Show <S01E02> 剧场版.mkv", { start: 6, end: 12 }),
    "Show &lt;<mark class=\"se-token\">S01E02</mark>&gt; 剧场版.mkv"
  );
  assert.equal(markSeasonEpisodeHtml("普通<名>.mkv", null), "普通&lt;名&gt;.mkv");
});

test("markSeasonEpisodeValueHtml：按最终季集值大小写不敏感命中", () => {
  assert.equal(
    markSeasonEpisodeValueHtml("show s01e02.mkv", "S01E02"),
    "show <mark class=\"se-token\">s01e02</mark>.mkv"
  );
  assert.equal(markSeasonEpisodeValueHtml("电影.2023.mkv", ""), "电影.2023.mkv");
  assert.equal(markSeasonEpisodeValueHtml("电影.2023.mkv", "S01E02"), "电影.2023.mkv");
  // 目标路径里的 Season 目录不带季集编号，保持原样；文件名部分命中
  assert.equal(
    markSeasonEpisodeValueHtml("剧名 (2020)/Season 1/剧名 S01E01.mkv", "S01E01"),
    "剧名 (2020)/Season 1/剧名 <mark class=\"se-token\">S01E01</mark>.mkv"
  );
});

await chain;
console.log(`\n整理季集高亮测试通过：${passed} 项`);
