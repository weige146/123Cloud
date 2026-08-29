const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cloud123", {
  isDesktop: true,
  getInfo: () => ipcRenderer.invoke("app:getInfo"),
  openDataDir: () => ipcRenderer.invoke("app:openDataDir"),
  getLogs: () => ipcRenderer.invoke("app:getLogs"),
  getPortConfig: () => ipcRenderer.invoke("app:getPortConfig"),
  setPortConfig: (payload) => ipcRenderer.invoke("app:setPortConfig", payload),
  onLogLines: (callback) => {
    ipcRenderer.on("log:lines", (_event, lines) => callback(lines));
  },
  onBackendStatus: (callback) => {
    ipcRenderer.on("backend:status", (_event, payload) => callback(payload));
  },
});
