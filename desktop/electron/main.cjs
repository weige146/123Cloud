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

const backend = new BackendManager();
let mainWindow = null;
let backendPort = 0;
let dataDir = "";
let fixedPort = null;

function configPath() {
  return path.join(dataDir, "config.json");
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
