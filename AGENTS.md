# AGENTS.md — 项目约束（AI 助手与维护者必读）

## 工作流铁律：先本地打包，测试确认后再提交

1. **任何代码改动完成后，先在本地跑测试并打包出可安装的 DMG / 安装包**，把产物路径交给维护者实测。
2. **维护者实测确认没问题之前，禁止 `git commit`、`git push`、打 tag。**
3. 发版（`git tag vX.Y.Z`）必须在本地安装包验收通过之后进行；tag 一旦推送，CI 会自动创建公开 Release。

### 本地测试 + 打包命令速查（macOS）

```bash
# 1. 后端测试
cd desktop/backend
.venv/bin/python -m pytest tests/ -q

# 2. 前端构建（产物内嵌进后端二进制）
cd ../web && npm ci && npm run build

# 3. PyInstaller 打后端侧车
cd ..
python -m PyInstaller backend.spec --noconfirm --distpath backend-dist --workpath backend-build

# 4. Electron 出包（产物在 desktop/release/）
npm ci && npm run dist:mac
```

Windows 包无法在 macOS 上交叉构建（PyInstaller 限制），由 CI 在 Windows runner 上构建。

## 油猴脚本版本号规则（只递增，不回退）

- 脚本文件：`油猴脚本/123-helper.user.js`，版本号写在头部 `// @version`。
- 发布地址：https://greasyfork.org/zh-CN/scripts/592236-123-%E5%8A%A9%E6%89%8B
- **铁律：本地开发版本号只准在「Greasy Fork 已发布版本」基础上递增，且一次只 +1 个补丁号（x.y.z → x.y.z+1）。**
  - 例：线上现在是 `1.2.4`，本次开发/发布就是 `1.2.5`；再下一次 `1.2.6`。
  - 禁止跳号（`1.2.4 → 1.3.0`）、禁止回退、禁止与线上版本持平。
- 每次改动脚本准备发布前，先打开上面的 Greasy Fork 页面确认「版本」字段的当前线上值（以页面元信息的"版本：x.y.z"为准，不要相信页面正文/文档里手写的版本号，那里可能过时），再据此 +1 写回 `@version`。
- 当前线上版本：`1.2.4`（更新于 2026-09-02），因此下一版为 **`1.2.5`**。

## 架构备忘

- `desktop/backend/app/main.py` — FastAPI 路由层；投稿/搬运的统一分流入口是 `route_submission_text`（油猴脚本 `POST /api/submission/submit` 与后台提交共用）。
- `desktop/backend/app/transfer_service.py` — 搬运任务调度门面：入队、去重、并发、暂停窗口、115 账号池健康、直链轮换、通知钩子。
- `desktop/backend/app/transfer_pipeline.py` — 单次搬运的六阶段管线：解析 → 规划 → 秒传 → 离线 → 等待 → 收尾；`OfflineDownloadManager` 滚动提交 + 统一等待（同时在 123 排队的离线任务数上限 = 后台"并发"配置 1-5，完成一个自动补交；完成判定 = 列目录 + 大小比对）。
- `desktop/backend/app/logsetup.py` — 统一日志：控制台 + 落盘轮转 + 内存环形缓冲（`GET /api/logs`）。应用日志必须是**大白话流程叙述**，第三方库一律降噪到 WARNING。
- Telegram 有两条通道：**投稿入口走 HTTP**（油猴脚本 `POST /api/submission/submit`，与后台共用 `route_submission_text` 分流），**最小化轮询**（`telegram_callback_polling_loop`）只处理草稿预览按钮回调和按钮触发的后续输入，不接收投稿文本；搬运任务通知、投稿预览、频道发布均为出站。
- 油猴脚本（`油猴脚本/123-helper.user.js`）不改动：它对 `/api/submission/submit` 的响应有硬校验（`ok===true` 且 `draftCount`、`sentCount` 必须等于批次条数），后端必须保持这个响应契约。
