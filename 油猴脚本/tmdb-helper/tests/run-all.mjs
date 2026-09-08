#!/usr/bin/env node
/**
 * TMDB 助手回归测试统一入口：逐个执行本目录下的 *.test.mjs，汇总结果。
 *
 * 用法：
 *   node 油猴脚本/tmdb-helper/tests/run-all.mjs
 *   （或在该目录下 npm test；首次运行需先 npm install 装 jsdom）
 *
 * 说明：
 * - tmdb-helper.test.mjs：纯逻辑回归（解析、排期引擎、数据源、字段匹配、payload）。
 * - tmdb-ui-smoke.test.mjs：jsdom 里引导面板的 UI 冒烟（依赖 jsdom）。
 * - 两者都从 ../tmdb-helper.user.js 里按标记切出片段，在 node:vm 沙箱里跑，
 *   不需要浏览器，除豆瓣解析用 jsdom 外不联网。
 */
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(here)
  .filter((name) => name.endsWith(".test.mjs"))
  .sort();

if (!files.length) {
  console.error("没有找到任何 .test.mjs");
  process.exit(1);
}

let failed = 0;
for (const file of files) {
  const started = Date.now();
  const result = spawnSync(process.execPath, [join(here, file)], { stdio: "pipe" });
  const cost = ((Date.now() - started) / 1000).toFixed(2);
  if (result.status === 0) {
    console.log(`  \u001b[32m✓\u001b[0m ${file} (${cost}s)`);
  } else {
    failed += 1;
    console.log(`  \u001b[31m✗\u001b[0m ${file} (${cost}s)`);
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
    if (output) console.log(output.split("\n").map((line) => `      ${line}`).join("\n"));
  }
}

console.log(`\n共 ${files.length} 个测试文件，通过 ${files.length - failed}，失败 ${failed}`);
process.exit(failed ? 1 : 0);
