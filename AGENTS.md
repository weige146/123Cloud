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

- 脚本文件：`油猴脚本/123-helper/123-helper.user.js`（各脚本一个同名文件夹，内含脚本本体、README 与 tests），版本号写在头部 `// @version`。
- 发布地址：https://greasyfork.org/zh-CN/scripts/592236-123-%E5%8A%A9%E6%89%8B
- **铁律：本地开发版本号只准在「Greasy Fork 已发布版本」基础上递增，且一次只 +1 个补丁号（x.y.z → x.y.z+1）。**
  - 例：线上现在是 `1.2.4`，本次开发/发布就是 `1.2.5`；再下一次 `1.2.6`。
  - 禁止跳号（`1.2.4 → 1.3.0`）、禁止回退、禁止与线上版本持平。
- 每次改动脚本准备发布前，先打开上面的 Greasy Fork 页面确认「版本」字段的当前线上值（以页面元信息的"版本：x.y.z"为准，不要相信页面正文/文档里手写的版本号，那里可能过时），再据此 +1 写回 `@version`。
- 当前线上版本：`1.2.8`（核对于 2026-09-07），本地已就绪待发布版本为 **`1.2.9`**（fileInfos 33 条截断补查、分享接口限流重试、TMDB 校准与媒体整理增强，另加：选中项出口统一按文件名自然排序——修复批量重命名预览/编号规则跟随勾选点击顺序错乱）。

## 架构备忘

- `desktop/backend/app/main.py` — FastAPI 路由层；投稿/搬运的统一分流入口是 `route_submission_text`（油猴脚本 `POST /api/submission/submit` 与后台提交共用）。
- `desktop/backend/app/transfer_service.py` — 搬运任务调度门面：入队、去重、并发、暂停窗口、115 账号池健康、直链轮换、通知钩子。
- `desktop/backend/app/transfer_pipeline.py` — 单次搬运的六阶段管线：解析 → 规划 → 秒传 → 离线 → 等待 → 收尾；`OfflineDownloadManager` 滚动提交 + 统一等待（同时在 123 排队的离线任务数上限 = 后台"并发"配置 1-5，完成一个自动补交；完成判定 = 列目录 + 大小比对）。
- `desktop/backend/app/logsetup.py` — 统一日志：控制台 + 落盘轮转 + 内存环形缓冲（`GET /api/logs`）。应用日志必须是**大白话流程叙述**，第三方库一律降噪到 WARNING。
- `desktop/backend/app/telegram_session.py` — TG 用户 Session 获取：手机号 → 验证码 →（可选两步验证密码）→ StringSession，登录会话只在内存里活 10 分钟。配套接口在 main.py：`/api/submission/telegram/session/start|verify|cancel`；登录成功后 session 由后端直接写回投稿配置的 `telegramApi`，并调 `reset_telegram_client_state()` 让旧帖清理的 client 单例重建。
- 离线等待阶段**不打印心跳日志**：后台「离线轮询间隔」默认 15 秒，每轮一条「…秒后再检查」会把每个文件的成功/失败全部淹掉。现在只保留成功/失败；每 60 秒做一次静默存档，仅用于及时感知任务被删除/取消。
- Telegram 有两条通道：**投稿入口走 HTTP**（油猴脚本 `POST /api/submission/submit`，与后台共用 `route_submission_text` 分流），**最小化轮询**（`telegram_callback_polling_loop`）只处理草稿预览按钮回调和按钮触发的后续输入，不接收投稿文本；搬运任务通知、投稿预览、频道发布均为出站。
- `desktop/backend/app/movie_library_db.py` — 影库数据库层（**数据库优先架构**）：三张表 `library_sources`（来源）/ `library_works`（作品，dir 主键）/ `library_work_files`（作品内文件），外加 `library_share_history`（提取历史，重启不清空），都在同一个 `cloud123.db`（WAL）。导入规则：dir 已存在则跳过（保留先入库的），同名来源重新导入 = 先清该来源旧数据再插（可更新），删除来源级联删其作品与文件。搜索走 `norm_title/pinyin` 索引 + SQL 分页，分类 GROUP BY、统计 SUM，导入时就算好拼音与归一化标题。
- `desktop/backend/app/movie_library.py` — 影库解析/格式工具（移植自"123云盘影库搜索-本地版"）：只保留纯函数（`parse_library_content` 等 123FastLink / 123pan-strm / V1/V2 秒传文本 / `.123share` 解析，`{tmdb-N}` 与 `[tmdb-N]` 都认）、`aggregate_works` 按 tmdb 标记聚合（Season 并入）、`split_category`、`fmt_size`、拼音键。**已删除** `Library`/`LibraryManager`/`MovieLibraryService`/watcher：不再有"扫描指定文件夹 + mtime 热重载 + kv 索引缓存"那套，用户手动选 JSON 入库后源 JSON 删掉也能查。
- 配套 `share_extractor.py`（分享链接提取，**完成后通过 `importer` 回调直接入库**，不再落盘 JSON；断点续传目录改到 `DATA_DIR/library_checkpoints`）与 `library_transfer.py`（复用已授权 123 OpenAPI 的 `md5_reuse`+`ensure_path` 转存到云盘）。
- 路由在 main.py `/api/library/*`：数据接口（search/files/export/categories/share/transfer）在配置了 `movieLibrary.token` 后必须带令牌（query `token=` 或 Bearer，恒时比较）；`GET /api/library/config` 的明文令牌只回给 127.0.0.1 来源。导入入口三个：`POST /api/library/import`（浏览器上传原始内容）、`POST /api/library/import/paths`（桌面端文件选择器多选，后端按路径读）、`POST /api/library/import/dir`（文件夹一次性批量导入，递归子目录、跳过 `_checkpoints`/隐藏目录、深度 ≤3，不驻留监控）；来源管理 `GET /api/library/sources` + `POST /api/library/sources/delete`（也支持 DELETE 方法）。`POST /api/library/scan` 与配置里的 `folder`/`autoScan`/`intervalSec` 已移除（导出目录 `exportDir` 保留，留空则落到 `DATA_DIR/秒传文件导出`）。影库接口面向油猴脚本"随时随地搜索转存"，与投稿接口同源同端口（`设置 → 服务端口`），客户端保持默认 127.0.0.1 监听。海报与 TMDB 详情走后端代理（`/api/library/poster|tmdb/{id}`，客户端投稿配置的 TMDB TOKEN，sqlite kv 缓存）。
- 油猴脚本（`油猴脚本/123-helper/123-helper.user.js`）不改动：它对 `/api/submission/submit` 的响应有硬校验（`ok===true` 且 `draftCount`、`sentCount` 必须等于批次条数），后端必须保持这个响应契约。
