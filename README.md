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

1. 改完代码推送 `main` → Actions 先跑测试与构建校验（Artifacts 供内部测试，需登录下载）；
2. 发版时打标签：`git tag v1.2.3 && git push origin v1.2.3` → 云端用标签号作为版本号构建双平台安装包，自动创建 Release 并附上安装包与更新日志；
3. 签名接入、开发模式与目录说明见 [desktop/README.md](desktop/README.md)。

## 功能

- **投稿机器人**：Telegram Bot 私聊投稿，123 分享链接 / 秒传链接生成草稿，自动补充 TMDB 识别、豆瓣评分、海报、资源信息和路由频道；Bot 私聊预览后一键发布；频道配置按 UID 隔离。
- **投稿展示**：投稿模板、片源备注、来源标签和分享按钮的展示配置与预览。
- **115 搬运**：115 分享 / 本地盘搬运到 123 云盘，优先秒传，必要时转为 123 OpenAPI 离线下载；支持 Cookie 池、并发、暂停时段，以及 Telegram 直接发送 115/123 分享链接转存。
- **115 助手**：单账号提交离线磁力 / ed2k，定时清理 115 回收站（后台循环 + Bot `/recycle` 命令）。
- **115 Cookie**：扫码获取 115 Cookie，写入助手 / 搬运 Cookie 池。
- **配套油猴脚本**：`油猴脚本/123-helper.user.js`（Tampermonkey 安装），增强 123 云盘网页端的文件与分享管理，并可把网页上的分享链接一键推送为客户端投稿草稿。
- **应用内日志**：设置页实时查看后端运行日志（内存保留最近 10000 行，并轮转落盘到 `data/logs/backend.log`，5MB×3 份）；服务端口可在设置里固定。

## 目录

```text
123Cloud/
├── desktop/          # 客户端项目（自包含）
│   ├── backend/      # FastAPI 网关（投稿、115 搬运/助手/Cookie、会话持久化）
│   ├── web/          # Vue 3 液态玻璃前端
│   ├── electron/     # Electron 主进程 / 侧车管理 / 开发编排
│   ├── scripts/      # 本地测试脚本
│   ├── build/        # 应用图标（含 logo 源图与生成脚本）
│   └── release/      # 出包产物（CI 生成）
└── 油猴脚本/          # 配套 123 云盘网页增强脚本
```
