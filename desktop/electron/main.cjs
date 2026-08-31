const { app, BrowserWindow, Menu, ipcMain, shell, dialog } = require("electron");
const fs = require("fs");
const path = require("path");
const net = require("net");
const { BackendManager } = require("./backend.cjs");

const DEV_URL = process.env.CLOUD123_DEV_URL || "";
const isDev = Boolean(DEV_URL);

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
  } else {
    // The real /admin URL is loaded once the sidecar reports healthy.
    mainWindow.loadURL("about:blank");
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function buildMenu() {
  const template = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { role: "editMenu" },
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
  app.quit();
});

app.on("before-quit", async (event) => {
  if (!backend.stopping) {
    event.preventDefault();
    await backend.stop();
    app.quit();
  }
});
