#!/usr/bin/env node
/**
 * 123 助手回归测试统一入口：逐个执行本目录下的 *.test.mjs，汇总结果。
 *
 * 用法：
 *   node 油猴脚本/123-helper/tests/run-all.mjs
 *   （或在该目录下 npm test）
 *
 * 说明：
 * - 每个 *.test.mjs 都是自包含的：直接从 ../123-helper.user.js 里按标记切出
 *   纯逻辑片段，在 node:vm 沙箱里用假 API 驱动，不需要浏览器、不需要网络。
 * - 任一用例失败即以非 0 退出码结束，便于接 CI。
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
