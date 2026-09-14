const { app, BrowserWindow, Menu, ipcMain, shell, dialog, nativeTheme } = require("electron");
const fs = require("fs");
const path = require("path");
const net = require("net");
const { BackendManager } = require("./backend.cjs");

const DEV_URL = process.env.CLOUD123_DEV_URL || "";
const isDev = Boolean(DEV_URL);

// ===== 内存占用精简（Chromium 进程级开关）=====
// process-per-site：主窗口与 OAuth 弹窗同源，合并进同一个渲染进程，
// 少一个常驻 renderer（约省 60-120MB）。
app.commandLine.appendSwitch("process-per-site");
// 收紧渲染进程 V8 老生代上限（默认 ~4GB 按需增长，导致 RSS 虚高）：
// 本应用最重的路径是影库整包 JSON 解析，768MB 足够并促使 GC 更勤快。
app.commandLine.appendSwitch("js-flags", "--max-old-space-size=768");
// 后台节流确保开启（窗口失焦时降计时器/动画频率）
app.commandLine.appendSwitch("enable-features", "BackgroundThrottling");
// 启动时清一次 Chromium 磁盘缓存（陈旧缓存只会白占磁盘，不影响登录态）

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

// 数据目录与应用名解耦：改名 / 重装 / 自动更新都不再丢配置和数据库。
// 旧版本把数据存在「随 package.json name 变化」的 userData 里，换名后配置全丢，
// 这里固定到 appData/123Cloud，并把旧目录里的 config.json / cloud123.db 迁移过来。
const DATA_DIR_NAME = isDev ? "123Cloud-dev" : "123Cloud";
const LEGACY_DATA_DIRS = ["cloud123-toolkit-electron", "cloud123-desktop", "Electron"];

function stableDataDir() {
  return path.join(app.getPath("appData"), DATA_DIR_NAME);
}

function migrateLegacyData(stableDir) {
  try {
    if (fs.existsSync(path.join(stableDir, "cloud123.db"))) return;
    for (const legacyName of LEGACY_DATA_DIRS) {
      const legacyDir = path.join(app.getPath("appData"), legacyName);
      const candidates = [path.join(legacyDir, "data"), legacyDir];
      for (const candidate of candidates) {
        if (!fs.existsSync(path.join(candidate, "cloud123.db"))) continue;
        fs.mkdirSync(stableDir, { recursive: true });
        for (const file of ["cloud123.db", "cloud123.db-wal", "cloud123.db-shm", "config.json"]) {
          const from = path.join(candidate, file);
          const to = path.join(stableDir, file);
          if (fs.existsSync(from)) fs.copyFileSync(from, to);
        }
        console.error(`[123cloud] migrated legacy data from ${candidate}`);
        return;
      }
    }
  } catch (error) {
    console.error("[123cloud] legacy data migration failed:", error);
  }
}

const stableDir = stableDataDir();
migrateLegacyData(stableDir);
fs.mkdirSync(stableDir, { recursive: true });
app.setPath("userData", stableDir);

const backend = new BackendManager();
let mainWindow = null;
// 用户主动退出（Cmd+Q / 菜单退出 / 更新安装）时为 true；关窗保活时不拦截
let isQuitting = false;
let backendPort = 0;
let dataDir = "";
let fixedPort = null;

// ===== 自动更新 =====
// GitHub 有新 Release 时自动检测并后台下载；macOS 无正式签名（ad-hoc），
// Squirrel 静默换包不可用，quitAndInstall 会退化为打开已下载的 DMG 引导拖装；
// Windows NSIS 为静默安装。所有事件同步给渲染层，由设置页展示状态。
let updateState = { status: "idle", info: null, error: "" };
let updateTimer = null;
let updateCheckRunner = null;
let lastSeenLatest = "";

const RELEASES_LATEST_API = "https://api.github.com/repos/weige146/123Cloud/releases/latest";

function sendUpdateStatus() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("update:status", updateState);
  }
}

function setUpdateStatus(status, extra = {}) {
  updateState = { ...updateState, ...extra, status, error: extra.error || "" };
  sendUpdateStatus();
}

function shortError(error) {
  // HttpError 会把整段响应头都拼进 message，只保留第一行可读摘要
  return String((error && error.message) || error).split("\n")[0].slice(0, 200);
}

async function latestReleaseVersion() {
  const response = await fetch(RELEASES_LATEST_API, {
    headers: { "User-Agent": "123Cloud-Desktop", Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(`GitHub API HTTP ${response.status}`);
  const data = await response.json();
  return String(data.tag_name || "").replace(/^v/i, "").trim();
}

function isNewerVersion(candidate, current) {
  const parse = (value) => String(value || "").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const a = parse(candidate);
  const b = parse(current);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const diff = (a[index] || 0) - (b[index] || 0);
    if (diff) return diff > 0;
  }
  return false;
}

function setupAutoUpdater() {
  if (!app.isPackaged || isDev) return;
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (error) {
    console.error("[123cloud] electron-updater unavailable:", error);
    return;
  }
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on("checking-for-update", () => setUpdateStatus("checking"));
  autoUpdater.on("update-available", (info) => setUpdateStatus("downloading", { info: { version: info.version } }));
  autoUpdater.on("update-not-available", (info) => setUpdateStatus("none", { info: { version: info && info.version } }));
  autoUpdater.on("download-progress", (progress) => {
    updateState = { ...updateState, status: "downloading", percent: Math.round(progress.percent || 0) };
    sendUpdateStatus();
  });
  autoUpdater.on("update-downloaded", (info) => setUpdateStatus("downloaded", { info: { version: info && info.version } }));
  autoUpdater.on("error", (error) => {
    // 错误可能只通过事件报告（promise 不 reject），也可能两条路径都触发；
    // 已处于更明确的状态时不覆盖
    if (["update-manual", "downloaded", "installing"].includes(updateState.status)) return;
    classifyUpdaterError(error);
  });

  // 缺 latest*.yml（如 v1.0.2 这类手工发布的版本）→ 引导去发布页；其余才算失败
  function classifyUpdaterError(error) {
    if (lastSeenLatest && isNewerVersion(lastSeenLatest, app.getVersion())) {
      setUpdateStatus("update-manual", { info: { version: lastSeenLatest }, error: shortError(error) });
    } else {
      setUpdateStatus("error", { error: shortError(error) });
    }
  }

  async function runUpdateCheck() {
    try {
      lastSeenLatest = await latestReleaseVersion();
      if (lastSeenLatest && !isNewerVersion(lastSeenLatest, app.getVersion())) {
        setUpdateStatus("none", { info: { version: lastSeenLatest } });
        return;
      }
    } catch (error) {
      // 拿不到最新版本号（离线等）时仍尝试 updater：有元数据就能走通
      console.error("[123cloud] release lookup failed:", error);
    }
    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      console.error("[123cloud] update check failed:", error);
      classifyUpdaterError(error);
    }
  }

  setTimeout(() => { runUpdateCheck().catch(() => {}); }, 10_000);
  updateTimer = setInterval(() => { runUpdateCheck().catch(() => {}); }, 6 * 60 * 60 * 1000);
  updateCheckRunner = runUpdateCheck;
}

function configPath() {
  // 不能叫 config.json：后端 SessionStore 启动时会把 data_dir/config.json
  // 当作 SQLite 之前的旧版配置导入数据库并删除文件（_migrate_legacy_json），
  // 端口设置曾被它反复吃掉。
  return path.join(dataDir, "desktop.json");
}

// 历史上端口存在 config.json；改名为 desktop.json 时把旧文件里的端口搬过来。
function migratePortConfigFile() {
  try {
    const legacy = path.join(dataDir, "config.json");
    if (!fs.existsSync(legacy)) return;
    if (!fs.existsSync(configPath())) {
      const raw = JSON.parse(fs.readFileSync(legacy, "utf8"));
      const port = Number(raw && raw.port);
      if (Number.isInteger(port) && port >= 1024 && port <= 65535) {
        fs.mkdirSync(dataDir, { recursive: true });
        fs.writeFileSync(configPath(), JSON.stringify({ port }, null, 2));
      }
    }
    fs.unlinkSync(legacy);
  } catch (error) {
    console.error("[123cloud] port config migration failed:", error);
  }
}

function readPortConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), "utf8"));
    const port = Number(raw.port);
    if (Number.isInteger(port) && port >= 1024 && port <= 65535) return port;
  } catch (_) { /* no config yet */ }
  return null;
}


function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 1000,
    minHeight: 680,
    show: false,
    backgroundColor: "#0a0c12",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(`http://127.0.0.1:${backendPort}`)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`http://127.0.0.1:${backendPort}`) && !url.startsWith(DEV_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  const target = isDev ? DEV_URL : `${backend.baseUrl}/admin`;
  if (isDev) {
    mainWindow.loadURL(target);
  } else if (backend.port) {
    // 关窗保活后由 activate 重建窗口：后端早已就绪，启动时的健康加载只跑一次不会再来。
    // 必须在这里主动加载 /admin，否则新窗口停留在 about:blank 只剩深色底（用户看到的黑屏）。
    const loadAdmin = () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.loadURL(target).catch((error) => {
        console.error("[123cloud] reload admin after recreate failed:", error);
      });
    };
    backend.waitForHealth(30_000).then(loadAdmin).catch(loadAdmin);
  } else {
    // The real /admin URL is loaded once the sidecar reports healthy.
    mainWindow.loadURL("about:blank");
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  // 关窗保活（仅 macOS）：点红绿灯关闭只销毁窗口、释放渲染进程（~300MB），
  // Python 后端与油猴脚本依赖的本机服务继续跑；点 Dock 图标由 activate 重建窗口。
  // Cmd+Q / 菜单退出走 before-quit（isQuitting=true），不经过这里。
  mainWindow.on("close", (event) => {
    if (!isQuitting && process.platform === "darwin") {
      event.preventDefault();
      const win = mainWindow;
      mainWindow = null;
      // 同步 destroy 会让主进程崩溃（close 回调内销毁自身），必须错开一拍
      setImmediate(() => {
        try {
          win?.destroy();
          console.error("[123cloud] window destroyed to free renderer; backend keeps running");
        } catch (error) {
          console.error("[123cloud] window destroy failed:", error);
        }
      });
    }
  });
}

function broadcastThemePreference(value) {
  nativeTheme.themeSource = value;
  for (const win of BrowserWindow.getAllWindows()) {
    win.webContents.send("theme:preference", value);
  }
}

function buildMenu() {
  const appearanceSubmenu = [
    {
      label: "跟随系统",
      type: "radio",
      checked: nativeTheme.themeSource === "system",
      click: () => broadcastThemePreference("system"),
    },
    {
      label: "浅色",
      type: "radio",
      checked: nativeTheme.themeSource === "light",
      click: () => broadcastThemePreference("light"),
    },
    {
      label: "深色",
      type: "radio",
      checked: nativeTheme.themeSource === "dark",
      click: () => broadcastThemePreference("dark"),
    },
  ];
  const template = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { role: "editMenu" },
    {
      label: "外观",
      submenu: appearanceSubmenu,
    },
    {
      label: "视图",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc() {
  ipcMain.handle("app:getInfo", () => ({
    isDesktop: true,
    platform: process.platform,
    dataDir,
    port: backendPort,
    fixedPort,
    versions: {
      app: app.getVersion(),
      electron: process.versions.electron,
    },
  }));
  ipcMain.handle("app:getLogs", () => backend.getLogs());
  ipcMain.handle("app:getPortConfig", () => ({ port: fixedPort }));
  ipcMain.handle("app:setPortConfig", (_event, payload) => {
    const port = Number(payload && payload.port);
    let config = {};
    try { config = JSON.parse(fs.readFileSync(configPath(), "utf8")) || {}; } catch (_) {}
    if (Number.isInteger(port) && port >= 1024 && port <= 65535) {
      config.port = port;
    } else {
      delete config.port;
    }
    fs.mkdirSync(dataDir, { recursive: true });
    fs.writeFileSync(configPath(), JSON.stringify(config, null, 2));
    fixedPort = readPortConfig();
    return { port: fixedPort };
  });
  ipcMain.handle("app:openDataDir", async () => {
    const result = await shell.openPath(dataDir);
    return result || "";
  });
  // 系统原生选文件夹（影库目录等场景）；关闭/取消返回 { cancelled: true }
  ipcMain.handle("app:pickFolder", async (_event, payload) => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: String((payload && payload.title) || "选择文件夹"),
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths.length) return { cancelled: true };
    return { path: result.filePaths[0] };
  });
  // 系统原生多选文件（影库 JSON 导入）
  ipcMain.handle("app:pickFiles", async (_event, payload) => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: String((payload && payload.title) || "选择影库文件"),
      properties: ["openFile", "multiSelections"],
      filters: (payload && payload.filters) || [
        { name: "影库文件", extensions: ["json", "txt", "123share", "123fastlink"] },
      ],
    });
    if (result.canceled || !result.filePaths.length) return { cancelled: true };
    return { paths: result.filePaths };
  });
  // 123 OAuth 授权弹窗：加载 123 官方授权页（账号密码在官方页输入），
  // 监听到跳回授权中转站回调地址（含 ?code= 或 #token）即截获并回传渲染层。
  let pan123OauthWindow = null;
  ipcMain.handle("app:openPan123Oauth", (_event, payload) => {
    const authorizeUrl = String((payload && payload.authorizeUrl) || "");
    const redirectUri = String((payload && payload.redirectUri) || "https://api.oplist.org/123cloud/callback");
    if (!/^https:\/\//i.test(authorizeUrl)) return Promise.resolve({ error: "授权地址无效" });
    if (pan123OauthWindow && !pan123OauthWindow.isDestroyed()) {
      try { pan123OauthWindow.destroy(); } catch (_) {}
    }
    return new Promise((resolve) => {
      let settled = false;
      const done = (result) => {
        if (settled) return;
        settled = true;
        resolve(result);
      };
      pan123OauthWindow = new BrowserWindow({
        width: 960,
        height: 740,
        title: "123 云盘授权登录",
        autoHideMenuBar: true,
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          spellcheck: false,
          partition: "persist:pan123oauth",
        },
      });
      const contents = pan123OauthWindow.webContents;
      const intercept = (event, url) => {
        if (typeof url !== "string" || !url.startsWith(redirectUri)) return;
        event.preventDefault();
        done({ callbackUrl: url });
        try { pan123OauthWindow.destroy(); } catch (_) {}
      };
      contents.on("will-navigate", intercept);
      contents.on("will-redirect", intercept);
      // 兜底：回调若被 302 到带 #token 的最终页面，直接从地址栏截获
      const captureFragment = (_event, url) => {
        if (settled || typeof url !== "string") return;
        if (url.startsWith(redirectUri) || (url.startsWith("https://api.oplist.org") && url.includes("#"))) {
          done({ callbackUrl: url });
          try { pan123OauthWindow.destroy(); } catch (_) {}
        }
      };
      contents.on("did-navigate", captureFragment);
      contents.on("did-navigate-in-page", captureFragment);
      pan123OauthWindow.on("closed", () => {
        pan123OauthWindow = null;
        done({ cancelled: true });
      });
      pan123OauthWindow.loadURL(authorizeUrl);
    });
  });
  // 端口等需要重启生效的配置保存后调用：停后端 → relaunch → 退出
  ipcMain.handle("app:relaunchApp", async () => {
    try {
      await backend.stop();
    } catch (error) {
      console.error("[123cloud] backend stop before relaunch failed:", error);
    }
    app.relaunch();
    app.exit(0);
    return true;
  });
  ipcMain.handle("app:relaunchAfterBackendReady", async () => {
    try {
      await backend.waitForHealth(30_000);
      if (mainWindow) mainWindow.loadURL(isDev ? DEV_URL : `${backend.baseUrl}/admin`);
      return true;
    } catch (error) {
      dialog.showErrorBox("后端未就绪", String(error));
      return false;
    }
  });
  ipcMain.handle("app:getUpdateState", () => updateState);
  ipcMain.handle("app:checkForUpdates", async () => {
    if (!app.isPackaged || isDev) {
      setUpdateStatus("error", { error: "开发模式不支持检查更新" });
      return updateState;
    }
    setUpdateStatus("checking");
    if (updateCheckRunner) await updateCheckRunner().catch(() => {});
    return updateState;
  });
  ipcMain.handle("app:installUpdate", () => {
    if (updateState.status !== "downloaded") return false;
    try {
      const { autoUpdater } = require("electron-updater");
      setUpdateStatus("installing");
      // Windows：静默安装并在完成后自动启动新版本；macOS：退出后打开已下载的 DMG
      autoUpdater.quitAndInstall(true, true);
    } catch (error) {
      setUpdateStatus("error", { error: String((error && error.message) || error) });
    }
    return true;
  });
}

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.whenReady().then(async () => {
  console.error("[123cloud] whenReady");
  dataDir = app.getPath("userData");
  // 启动清一次 HTTP 磁盘缓存；此后每小时若窗口失焦再清（登录态在 cookie/后端，缓存可随时丢）
  try {
    const idleCacheSweep = () => {
      if (mainWindow && !mainWindow.isFocused()) return; // 聚焦时不清，避免体感卡顿
      for (const win of BrowserWindow.getAllWindows()) {
        win.webContents.session.clearCache().catch(() => undefined);
      }
    };
    idleCacheSweep();
    const cacheTimer = setInterval(idleCacheSweep, 60 * 60 * 1000);
    app.on("will-quit", () => clearInterval(cacheTimer));
  } catch (error) {
    console.error("[123cloud] cache sweep failed:", error);
  }
  console.error("[123cloud] userData:", dataDir);
  migratePortConfigFile();
  registerIpc();
  buildMenu();

  createWindow();

  try {
    fixedPort = readPortConfig();
    if (fixedPort) {
      console.error("[123cloud] using fixed port:", fixedPort);
      backendPort = fixedPort;
    } else {
      backendPort = await getFreePort();
      console.error("[123cloud] free port:", backendPort);
    }
    await backend.start({ port: backendPort, dataDir });
    console.error("[123cloud] backend ready");
    // 外观菜单 radio 状态跟随 nativeTheme（菜单点击 / 系统外观变化都会触发）
    nativeTheme.on("updated", () => buildMenu());
  } catch (error) {
    console.error("[123cloud] backend failed:", error);
    dialog.showErrorBox("123Cloud 后端启动失败", `${error}\n\n最近日志：\n${backend.lastLogLines(30)}`);
    app.quit();
    return;
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(isDev ? DEV_URL : `${backend.baseUrl}/admin`);
  }

  backend.on("log", (lines) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("log:lines", lines);
    }
  });
  backend.on("crash", ({ restarts, delay }) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend:status", { state: "crash", restarts, delay });
    }
  });
  backend.on("dead", () => {
    dialog.showErrorBox("后端已停止", `Python 后端多次崩溃，应用将退出。\n\n最近日志：\n${backend.lastLogLines(30)}`);
    app.quit();
  });

  setupAutoUpdater();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

process.on("unhandledRejection", (reason) => {
  console.error("[123cloud] unhandled rejection:", reason);
});

app.on("window-all-closed", () => {
  // macOS：关窗后服务保活（油猴脚本仍在用本机接口），Dock 点击 activate 重建窗口；
  // 其他平台维持原行为：关窗即退出。
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  isQuitting = true;
});

app.on("before-quit", async (event) => {
  if (!backend.stopping) {
    event.preventDefault();
    await backend.stop();
    app.quit();
  }
});
