const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cloud123", {
  isDesktop: true,
  getInfo: () => ipcRenderer.invoke("app:getInfo"),
  openDataDir: () => ipcRenderer.invoke("app:openDataDir"),
  getLogs: () => ipcRenderer.invoke("app:getLogs"),
  getPortConfig: () => ipcRenderer.invoke("app:getPortConfig"),
  setPortConfig: (payload) => ipcRenderer.invoke("app:setPortConfig", payload),
  relaunchApp: () => ipcRenderer.invoke("app:relaunchApp"),
  onLogLines: (callback) => {
    ipcRenderer.on("log:lines", (_event, lines) => callback(lines));
  },
  onBackendStatus: (callback) => {
    ipcRenderer.on("backend:status", (_event, payload) => callback(payload));
  },
  getUpdateState: () => ipcRenderer.invoke("app:getUpdateState"),
  checkForUpdates: () => ipcRenderer.invoke("app:checkForUpdates"),
  installUpdate: () => ipcRenderer.invoke("app:installUpdate"),
  onUpdateStatus: (callback) => {
    ipcRenderer.on("update:status", (_event, payload) => callback(payload));
  },
});
