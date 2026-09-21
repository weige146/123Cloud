// 更新内容通知回归：@version 必须与 SCRIPT_CHANGELOG 头部一致（防止改了版本号忘了写更新说明），
// 以及「每个版本只弹一次、点我已知晓后记已读」的门控逻辑。
// 用法：node 油猴脚本/123-helper/tests/changelog-notice.test.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../123-helper.user.js", import.meta.url));
const raw = fs.readFileSync(scriptPath, "utf8");
const lines = raw.split("\n");
const slice = (fromMarker, toMarker) => {
  const start = lines.findIndex((line) => line.includes(fromMarker));
  const end = lines.findIndex((line) => line.includes(toMarker));
  if (start < 0 || end <= start) throw new Error(`bundle markers not found: ${fromMarker} .. ${toMarker}`);
  return lines.slice(start, end).join("\n");
};
const code = [
  slice("// src/core/utils.js", "// src/api.js"),
  slice("// src/api.js", "// src/core/categories.js"),
  slice("// src/menu.js", "// src/share-response.js")
].join("\n");
const driver = `;
globalThis.__mod = { SCRIPT_CHANGELOG, scriptVersion, maybeShowUpdateNotes, showChangelogDialog, changelogDialogHtml, CHANGELOG_SEEN_KEY };
`;

const stored = new Map();
const madeNodes = [];
const fakeElement = () => {
  const node = {
    attributes: {},
    children: [],
    listeners: {},
    setAttribute(key, value) {
      this.attributes[key] = value;
    },
    hasAttribute(key) {
      return Object.prototype.hasOwnProperty.call(this.attributes, key);
    },
    addEventListener(type, handler) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(handler);
    },
    removeEventListener() {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    remove() {
      this.removed = true;
    },
    fire(type) {
      for (const handler of this.listeners[type] || []) handler({ target: this });
    },
    set innerHTML(value) {
      this.html = value;
    },
    get innerHTML() {
      return this.html || "";
    }
  };
  madeNodes.push(node);
  return node;
};
const sandbox = {
  console, Date, Math, JSON, Number, String, Array, Object, Set, Map, RegExp, Intl, Symbol, Error,
  DOMException, BigInt, TextEncoder, TextDecoder, Promise, URL, URLSearchParams, structuredClone,
  location: { origin: "https://www.123pan.cn", hostname: "www.123pan.cn" },
  setTimeout, clearTimeout, AbortController,
  GM_getValue: (key, fallback) => (stored.has(key) ? stored.get(key) : fallback),
  GM_setValue: (key, value) => stored.set(key, value),
  document: {
    createElement: () => fakeElement(),
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => fakeElement(),
    documentElement: fakeElement()
  },
  fetch: () => Promise.reject(new Error("fetch 由用例注入"))
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code + driver, sandbox, { filename: "123-helper.user.js" });
const { SCRIPT_CHANGELOG, scriptVersion, maybeShowUpdateNotes, changelogDialogHtml } = sandbox.__mod;

// —— 用例 1：头部版本号与脚本 @version 一致（防止改版本忘了写更新说明） ——
{
  const header = raw.match(/\/\/ @version\s+([0-9]+\.[0-9]+\.[0-9]+)/);
  assert.ok(header, "脚本头部应有 @version");
  assert.equal(SCRIPT_CHANGELOG[0].version, header[1], "SCRIPT_CHANGELOG 头部条目必须与 @version 一致");
  assert.equal(scriptVersion(), header[1], "无 GM_info 时应回落到 changelog 头部版本");
  console.log(`ok 更新说明头部版本与 @version 一致（${header[1]}）`);
}

// —— 用例 2：新版本的 notes 不能是空的（弹空窗不如不弹） ——
{
  const entry = SCRIPT_CHANGELOG[0];
  assert.ok(Array.isArray(entry.notes) && entry.notes.length >= 3, "每个版本至少写 3 条更新要点");
  assert.ok(entry.notes.every((note) => String(note).length > 8), "更新要点不该是占位文本");
  console.log(`ok 本版更新说明含 ${entry.notes.length} 条要点`);
}

// —— 用例 3：存储门控（新版本弹一次；已读不再弹；没有记录的版本静默记已读） ——
{
  stored.clear();
  const version = scriptVersion();
  assert.equal(maybeShowUpdateNotes(), true, "首见新版本应弹更新内容");
  assert.notEqual(stored.get("Cloud123.Helper.SeenChangelog"), version, "未点「我已知晓」前不该提前记已读");
  stored.set("Cloud123.Helper.SeenChangelog", version);
  assert.equal(maybeShowUpdateNotes(), false, "同一版本不该再弹第二次");
  // changelog 里没有记录的版本（例如只改了 @version 忘了写说明）：静默记已读、不弹空窗
  sandbox.GM_info = { script: { version: "8.8.8" } };
  stored.set("Cloud123.Helper.SeenChangelog", "0.0.1");
  assert.equal(maybeShowUpdateNotes(), false, "没有更新说明时不该弹空窗");
  assert.equal(stored.get("Cloud123.Helper.SeenChangelog"), "8.8.8", "没有说明也应把已读版本记上，避免每次加载都判断");
  delete sandbox.GM_info;
  console.log("ok 更新内容按版本门控：弹一次、已读不再弹、无说明不弹空窗");
}

// —— 用例 4：弹窗文案（转义 + 只列最近几条） ——
{
  const html = changelogDialogHtml([{ version: "9.9.9", notes: ["<script>alert(1)</script>", "\u666E\u901A\u8981\u70B9"] }], "9.9.9");
  assert.ok(!html.includes("<script>alert(1)</script>"), "更新要点必须转义");
  assert.ok(html.includes("&lt;script&gt;"), "转义后应保留可见文本");
  assert.ok(html.includes("9.9.9"), "应显示版本号");
  assert.ok(html.includes("\u6211\u5DF2\u77E5\u6653"), "应有确认按钮");
  console.log("ok 更新内容弹窗文案安全（转义 + 版本 + 确认按钮）");
}

console.log("changelog-notice 全部通过");
