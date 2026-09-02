# 123Cloud

[![CI](https://github.com/weige146/123Cloud/actions/workflows/ci.yml/badge.svg)](https://github.com/weige146/123Cloud/actions/workflows/ci.yml)

123Cloud 是一个跑在自己电脑上的**网盘资源投稿 / 搬运工具箱**（Windows / macOS 桌面应用，Electron 壳 + Python 后端 + 液态玻璃 UI）。它把「115 网盘 → 123 云盘」的搬运、资源投稿的整理与频道发布、123 云盘网页端的增强操作收进一个客户端。

## 它能干什么

- **115 → 123 搬运**：把 115 分享链接（或本地秒传文件）自动搬到 123 云盘。走六阶段管线：解析 → 规划跳过已有文件 → 秒传 → 滚动提交 123 离线下载（并发上限可配 1-5，完成一个自动补交）→ 统一等待 → 汇总。支持 115 Cookie 池多号轮换、Cookie 失效自动冷却换号（分享已取消等分享本身失效的报错不会误冷却账号；客户端「115 中心」可查看冷却中的账号并一键清除停用限制）、暂停时段、任务去重、失败重试，全程大白话任务日志。
- **投稿机器人**：123 分享链接 / 秒传链接自动生成投稿草稿，自动补齐 TMDB 识别、豆瓣评分、海报、资源信息，并按发布组规则路由到对应频道；草稿确认后一键发布到 Telegram 频道。
- **投稿入口**：配套油猴脚本（见下）在 123 云盘网页端点分享即可推送到客户端——别人的分享自动进入搬运，自己的分享生成投稿草稿。
- **115 助手**：单账号提交离线磁力 / ed2k，定时清理 115 回收站。
- **115 Cookie**：扫码获取 115 Cookie，写入助手或搬运 Cookie 池。
- **应用内日志 / 控制台**：实时查看后端运行日志，轮转落盘，重启后仍可排查。

## 使用前提（账号与凭据）

应用在本机运行，直接用你自己的网盘账号干活，不同功能需要的凭据不同：

| 功能 | 需要准备 | 在哪里配置 |
| --- | --- | --- |
| 投稿机器人 | 123 云盘账号（账号密码登录）；TMDB API Token（识别海报 / 评分）；Telegram Bot Token | 客户端内登录 123 → 设置页 |
| 频道发布 / 通知 | Telegram Bot Token + API ID / API Hash（草稿预览、任务通知、频道发布均为出站） | 设置页 |
| **115 → 123 搬运** | **123 开放平台应用凭据（clientID / clientSecret）**——离线下载走 123 OpenAPI；115 账号 Cookie（支持多账号池，可在客户端扫码获取） | 后台「搬运」设置 |
| 115 助手 | 115 账号 Cookie（扫码获取） | 「115 Cookie」页 |
| 配套油猴脚本 | 安装 [Tampermonkey](https://www.tampermonkey.net/)，脚本设置里填客户端投稿地址 | 见[油猴脚本说明](油猴脚本/README.md) |

> [!IMPORTANT]
> **搬运必须在「搬运」设置里填入 123 开放平台的 clientID / clientSecret**（在 [123 开放平台](https://www.123pan.com/developer) 创建应用获取），否则离线下载任务无法提交。
>
> [!WARNING]
> 应用会保存网盘登录态、Cookie、Telegram Session 和 API 凭据。后端只监听本机 `127.0.0.1`，请勿将端口暴露到公网。

## 下载安装

正式版发布在 [Releases](../../releases) 页面，无需登录 GitHub 即可下载：

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| macOS (Apple Silicon) | `123Cloud-<版本>-macOS-arm64.dmg` | ad-hoc 签名，首次打开右键 →「打开」→「打开」 |
| Windows x64 | `123Cloud-<版本>-Windows-x64.exe` | 未签名，SmartScreen 弹窗点「仍要运行」 |

每个 Release 附带自动生成的更新日志。

## 配套油猴脚本

[`油猴脚本/123-helper.user.js`](油猴脚本/README.md)（Tampermonkey 安装）增强 123 云盘网页端：文件页全盘搜索、批量重命名、TMDB 媒体整理、文件清理、秒传工具箱（含二级秒传短链接、从云盘秒传文件直接转存）、批量分享与一键投稿。创建分享后可自动推送为客户端投稿草稿。

## 目录

```text
123Cloud/
├── desktop/          # 客户端项目（自包含）
│   ├── backend/      # FastAPI 后端（投稿分流、115 搬运/助手/Cookie、会话持久化）
│   ├── web/          # Vue 3 液态玻璃前端
│   ├── electron/     # Electron 主进程 / 侧车管理 / 开发编排
│   └── build/        # 应用图标（含 logo 源图与生成脚本）
└── 油猴脚本/          # 配套 123 云盘网页增强脚本
```

开发与发版约束见 [AGENTS.md](AGENTS.md)，客户端构建细节见 [desktop/README.md](desktop/README.md)。
