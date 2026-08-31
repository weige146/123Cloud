# 123Cloud

[![CI](https://github.com/weige146/123Cloud/actions/workflows/ci.yml/badge.svg)](https://github.com/weige146/123Cloud/actions/workflows/ci.yml)

123Cloud 是聚焦 **Telegram 投稿**与 **115 协作**的桌面客户端（Windows / macOS），采用 Electron 壳 + PyInstaller 后端侧车 + 液态玻璃 UI。整个项目都在 [`desktop/`](desktop/) 内。

> [!WARNING]
> 应用会保存网盘登录态、Cookie、Telegram Session 和 API 凭据。后端只监听本机 `127.0.0.1`，请勿将端口暴露到公网。

## 下载安装包（Releases）

正式版发布在 [Releases](../../releases) 页面，任何人无需登录 GitHub 即可直接下载：

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| macOS (Apple Silicon) | `123Cloud-<版本>-macOS-arm64.dmg` | ad-hoc 签名，首次打开右键 →「打开」→「打开」 |
| Windows x64 | `123Cloud-<版本>-Windows-x64.exe` | 未签名，SmartScreen 弹窗点「仍要运行」 |

每个 Release 附带自动生成的更新日志（changelog）。

## 发版流程（维护者）

1. 改完代码先在**本地打包并实测**（跑测试 → web 构建 → PyInstaller → electron-builder，命令见 [AGENTS.md](AGENTS.md)）——实测确认前不提交、不打标签；
2. 实测通过后提交推送 `main` → Actions 跑测试与构建校验（Artifacts 供内部测试，需登录下载）；
3. 发版时打标签：`git tag v1.2.3 && git push origin v1.2.3` → 云端用标签号作为版本号构建双平台安装包，自动创建 Release 并附上安装包与更新日志；
4. 签名接入、开发模式与目录说明见 [desktop/README.md](desktop/README.md)。

## 功能

- **投稿机器人**：123 分享链接 / 秒传链接生成草稿，自动补充 TMDB 识别、豆瓣评分、海报、资源信息和路由频道；草稿在后台"投稿草稿"页一键发布到频道；频道配置按 UID 隔离。
- **投稿入口**：配套油猴脚本在 123 云盘网页端点击分享即自动推送到客户端（`POST /api/submission/submit`）——别人的分享自动转存搬运，自己的分享生成投稿草稿；不再依赖 Telegram 轮询接收。Telegram 只用来收通知（任务排队/成败、Cookie 失效告警）和发投稿预览/频道消息。
- **115 搬运**：115 分享 / 本地盘搬运到 123 云盘，六阶段管线（解析 → 规划跳过已有 → 秒传 → 滚动提交 123 离线（并发上限 = "并发"配置，完成一个补一个）→ 统一等待 → 汇总）；支持 Cookie 池、失效冷却换号、暂停时段，任务日志全程大白话。
- **115 助手**：单账号提交离线磁力 / ed2k，定时清理 115 回收站（后台循环）。
- **115 Cookie**：扫码获取 115 Cookie，写入助手 / 搬运 Cookie 池。
- **配套油猴脚本**：`油猴脚本/123-helper.user.js`（Tampermonkey 安装），增强 123 云盘网页端的文件与分享管理，创建分享后自动推送到客户端。
- **应用内日志**：设置页实时查看后端运行日志（`/api/logs` 内存环形缓冲最近 10000 行，并轮转落盘到 `data/logs/backend.log`，5MB×3 份）；服务端口可在设置里固定。

## 目录

```text
123Cloud/
├── AGENTS.md         # 项目约束（先本地打包实测，再提交）
├── desktop/          # 客户端项目（自包含）
│   ├── backend/      # FastAPI 网关（投稿分流、115 搬运/助手/Cookie、会话持久化）
│   ├── web/          # Vue 3 液态玻璃前端
│   ├── electron/     # Electron 主进程 / 侧车管理 / 开发编排
│   ├── build/        # 应用图标（含 logo 源图与生成脚本）
│   └── release/      # 出包产物（本地/CI 生成）
└── 油猴脚本/          # 配套 123 云盘网页增强脚本
```
