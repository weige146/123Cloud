# 123Cloud Desktop（桌面客户端）

123Cloud 的 Windows / macOS 桌面应用：Electron 壳 + Python 后端（FastAPI）+ 液态玻璃前端，把网盘搬运、投稿发布等能力收进一个客户端。

## 功能介绍

### 115 → 123 搬运

把 115 分享链接 / 目录自动搬到 123 云盘，六阶段管线：解析 → 规划 → 秒传 → 离线下载 → 统一等待 → 收尾。

- SHA1 / MD5 秒传优先，秒不动的自动滚动提交 123 离线下载（并发 1-5 可配，完成一个补交一个）
- 断点恢复、超时自动重提、任务去重、失败重试
- 115 多账号 Cookie 池轮换，失效自动冷却换号
- 完成后可选自动删除 115 源文件（独立开关，安全闸门保护）

### 123 → 115 反向搬运

把 123 云盘的文件反向搬回 115：按内容指纹反查 115 SHA1，直接秒传入 115，无需重新下载上传。

### 共享 SHA1 秒传池（默认启用）

所有用户的搬运成果（内容指纹 + 文件名）自动进入共享池：你搬过的内容，别人搬运时直接秒传命中，反之亦然。

- 安装即用，零配置；每个用户独立 API Token，独立限流、可单独撤销
- 管理员 Token 的客户端额外开放「秒传池」入口（115 中心 → 秒传池）：搜索共享池内容目录，勾选后直接 SHA1 秒传到指定 123 文件夹
- 密钥在「设置 → 秒传池密钥」管理：默认用安装包内置的分发 Token（只加速搬运、无法搜索目录），粘贴管理员 Token 即可解锁搜索，「重置」恢复默认
- 共享池命中的秒传不写本地学习表、不自动删除 115 源文件（防投毒护栏）
- 服务异常自动熔断、静默降级，只影响秒传加速，不影响搬运本身
- 本地学习表始终兜底

### Telegram 投稿机器人

- 123 分享链接 / 秒传链接自动生成投稿草稿：TMDB 识别、豆瓣评分、海报、资源信息自动补齐
- 按发布组规则路由到对应 Telegram 频道，草稿确认后一键发布
- 配套油猴脚本（`油猴脚本/123-helper.user.js`）：在 123 网页端点分享即可推送投稿 / 触发搬运
- 应用内 TG Session 登录，草稿预览按钮交互

### 115 助手与 Cookie

- 单账号提交离线磁力 / ed2k 任务，定时清理 115 回收站
- 扫码获取 115 Cookie，写入助手或搬运 Cookie 池

### 运行日志

应用内「设置 → 运行日志」实时查看（内存环形缓冲最近 10000 行），同时轮转落盘（5MB × 3 份），重启后仍可排查。

## 安装

从 [Releases](https://github.com/weige146/123Cloud/releases) 下载对应平台安装包：

- **macOS**：未签名，首次打开右键 →「打开」→ 再点「打开」；若提示已损坏，执行 `xattr -cr "/Applications/123Cloud.app"`
- **Windows**：SmartScreen 弹窗时点「更多信息」→「仍要运行」

## 开发与构建

```bash
# 后端（Python 3.9+）
cd desktop/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -e '.[dev]'
.venv/bin/python -m pytest tests/ -q

# 一键起开发环境（venv 后端 :8321 → Vite :5174 → Electron 窗口）
cd desktop && npm install && npm run dev

# 本地出包：前端 → PyInstaller 侧车 → Electron
cd desktop/web && npm ci && npm run build && cd ..
python3 -m PyInstaller backend.spec --noconfirm --distpath backend-dist --workpath backend-build
npm ci && npm run dist:mac
```

推送 `main` 或打 `v*` 标签后，CI 自动跑测试并在 macOS / Windows 两个 runner 出包，tag 推送自动创建公开 Release。改动先本地打包实测、验收通过后再提交（见根目录 [AGENTS.md](../AGENTS.md)）。

## 主要目录

| 路径 | 说明 |
| --- | --- |
| `backend/` | FastAPI 后端源码（含 tests/） |
| `web/` | Vue 3 前端源码 |
| `electron/` | Electron 主进程 / 侧车管理 |
| `backend.spec` / `electron-builder.yml` | 打包配置 |
| `build/` | 应用图标 |
