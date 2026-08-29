# 123Cloud Desktop（Electron 壳）

把 123Cloud 的 Telegram 投稿、115 搬运、115 助手、115 Cookie 打包成 Windows / macOS 独立桌面应用。

## 架构

```
Electron 主进程 (electron/main.cjs)
 ├─ 单实例锁 → 选空闲端口 → 启动 PyInstaller 侧车 cloudgateway
 ├─ 轮询 /api/health 就绪后 → 窗口加载 http://127.0.0.1:{port}/admin
 └─ 退出时 SIGTERM 侧车；侧车崩溃按指数退避自动重启（≤5 次）
PyInstaller 侧车 (backend.spec → backend-dist/cloudgateway/)
 └─ FastAPI 后端原样打包，内部托管前端 SPA
前端 (../web) — 液态玻璃 UI，浏览器与桌面共用
```

- 侧车只监听 `127.0.0.1`，端口由壳动态注入（`CLOUD123_PORT`），也可在应用「设置 → 服务端口」固定。
- 数据目录由壳注入（`DATA_DIR`）：macOS `~/Library/Application Support/123Cloud/`，Windows `%APPDATA%/123Cloud/`。
- 运行日志在应用「设置 → 运行日志」实时查看（内存环形缓冲，最近 800 行，不写磁盘）。

## 开发模式

```bash
# 准备后端虚拟环境
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# 安装桌面依赖并启动（venv 后端(:8321) → Vite(:5174, HMR) → Electron 窗口）
npm install
npm run dev
```

## 构建（GitHub Actions 自动出包）

安装包由 [.github/workflows/ci.yml](../.github/workflows/ci.yml) 在 GitHub 上构建，本地不需要跑脚本：

1. 推送代码到 `main`（或打 `v*` 标签）自动触发；
2. `tests` job 先跑后端 pytest 与前端构建校验；
3. `package` job 在 macOS 与 Windows 两个 runner 上分别出包（PyInstaller 无法交叉编译，Windows 版必须由 Windows runner 构建）；
4. 到仓库 **Actions → 对应运行 → Artifacts** 下载 `123Cloud-macOS`（DMG）与 `123Cloud-Windows`（安装器 exe）。

## 首次打开（未签名）

安装包在 CI 上做了 ad-hoc 签名（无付费开发者证书），首次打开会提示「无法验证开发者」：

- **macOS**：右键 App →「打开」→ 再点「打开」即可；若仍提示已损坏（旧版安装包），执行 `xattr -cr "/Applications/123Cloud.app"`。
- **Windows**：SmartScreen 弹窗时点「更多信息」→「仍要运行」。

## 接入签名（可选）

在 GitHub 仓库 Secrets 配好证书后，于 workflow 的 electron-builder 步骤注入环境变量：

- macOS：`CSC_LINK` / `CSC_KEY_PASSWORD`（Developer ID 证书），并去掉 `electron-builder.yml` 中的 `identity: null`。
- Windows：`CSC_LINK` 指向 `.pfx` 代码签名证书。

## 目录说明

### 源代码与配置（保留，不要删）

| 路径 | 说明 |
| --- | --- |
| `backend/` | FastAPI 后端源码（`python -m app` 即侧车入口，含 tests/） |
| `web/` | Vue 3 液态玻璃前端源码（`dist/` 为其构建产物） |
| `electron/` | Electron 主进程 / 侧车管理 / preload / 开发编排 |
| `scripts/test_backend.sh` | 本地快速跑后端测试 |
| `build/` | 应用图标；`icon-source.jpg` 是 logo 源图，换图标就替换它再跑 `build/generate_icon.py` |
| `backend.spec` | PyInstaller 打包配置（内嵌前端） |
| `sidecar_entry.py` | 侧车打包入口 |
| `electron-builder.yml` | 安装包打包配置（DMG / NSIS） |
| `package.json` / `package-lock.json` | Electron 依赖清单 |

仓库根目录的 `油猴脚本/123-helper.user.js` 是配套的 123 云盘网页增强脚本（Tampermonkey 安装），可把网页端的分享链接一键推送为客户端投稿草稿。

### 自动生成（删了也没关系，CI / 构建流程会重新生成）

| 路径 | 是什么 | 如何再生成 |
| --- | --- | --- |
| `backend-build/` | PyInstaller 中间产物 | 出包流程自动生成 |
| `backend-dist/` | 打包好的后端侧车（electron-builder 打包时会用到） | 出包流程自动生成 |
| `release/` | 安装包产物（DMG / exe） | 出包流程自动生成 |
| `web/dist/` | 前端构建产物 | `cd web && npm run build` |
| `web/node_modules/`、`node_modules/` | npm 依赖 | `npm install` |
| `data/` | 开发模式跑后端时的本地数据（桌面应用用的是系统数据目录，与此无关） | 可直接删除 |
