/* Sidecar management for the packaged Python gateway.

   The shell never imports Python code — it launches the PyInstaller onedir
   binary (packaged) or the repo venv (dev mode) and waits for /api/health.
   Sidecar output is kept in an in-memory ring buffer (shown in the app's
   Settings page); nothing is written to disk. */

const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");
const { EventEmitter } = require("events");

const RESTART_MAX = 5;
// 对齐 tdr-123help 的 Web 日志队列容量（10000 条），避免刷屏把有用日志顶掉
const LOG_BUFFER_MAX_LINES = 10000;

// 共享SHA1秒传池的配置注入（键名 SHA1DB_* / SHA1_POOL_API_*，文件不进 git）。
// 优先级：真实环境变量 > 数据目录用户配置（用户可自己放 Token，免重装）> 包内内置配置。
// 包内文件：本地打包用 desktop/sha1db.env，CI 打包时由 Secret 生成，electron-builder 放进 Resources。
function _loadSha1dbEnvFile(filePath) {
  const values = {};
  let text;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch {
    return values;
  }
  for (const line of String(text).split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!key.startsWith("SHA1")) continue;
    const value = trimmed.slice(eq + 1).trim();
    if (value) values[key] = value;
  }
  return values;
}

function _sha1dbBundledEnvFile() {
  if (require("electron").app.isPackaged) {
    return path.join(process.resourcesPath, "sha1db.env");
  }
  return path.join(__dirname, "..", "sha1db.env");
}

class BackendManager extends EventEmitter {
  constructor() {
    super();
    this.child = null;
    this.port = 0;
    this.dataDir = "";
    this.stopping = false;
    this.restarts = 0;
    this.logLines = [];
    this._pendingLine = "";
  }

  get baseUrl() {
    return `http://127.0.0.1:${this.port}`;
  }

  sidecarPath() {
    const name = process.platform === "win32" ? "cloudgateway.exe" : "cloudgateway";
    return path.join(process.resourcesPath, "cloudgateway", name);
  }

  isPackagedSidecar() {
    if (!require("electron").app.isPackaged) return false;
    return fs.existsSync(this.sidecarPath());
  }

  start({ port, dataDir }) {
    this.port = port;
    this.dataDir = dataDir;
    this.stopping = false;
    this._log(`[shell] starting sidecar on port ${port}`);
    this._spawn();
    return this.waitForHealth(60_000);
  }

  getLogs() {
    return this.logLines.slice();
  }

  _log(line) {
    const text = String(line).replace(/\s+$/, "");
    if (!text) return;
    const stamped = `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} ${text}`;
    this.logLines.push(stamped);
    if (this.logLines.length > LOG_BUFFER_MAX_LINES) {
      this.logLines.splice(0, this.logLines.length - LOG_BUFFER_MAX_LINES);
    }
    this.emit("log", [stamped]);
  }

  _spawn() {
    let command, args, options;
    if (this.isPackagedSidecar()) {
      command = this.sidecarPath();
      args = [];
      options = { cwd: path.dirname(this.sidecarPath()) };
    } else {
      // Dev mode: run the repo backend from its virtualenv.
      const clientRoot = path.join(__dirname, "..");
      const python = process.platform === "win32"
        ? path.join(clientRoot, "backend", ".venv", "Scripts", "python.exe")
        : path.join(clientRoot, "backend", ".venv", "bin", "python");
      if (!fs.existsSync(python)) {
        const message = `Dev python not found: ${python}`;
        this.emit("error", message);
        this._log(`[shell] ${message}`);
        return;
      }
      command = python;
      args = ["-m", "app"];
      options = { cwd: path.join(clientRoot, "backend"), env: { ...process.env, PYTHONPATH: path.join(clientRoot, "backend") } };
    }
    options.env = {
      ...(options.env || process.env),
      CLOUD123_PORT: String(this.port),
      DATA_DIR: this.dataDir,
      LOG_LEVEL: "info",
    };
    // 共享秒传池配置：进程环境变量（已在上面的 base 里）> 数据目录用户配置 > 包内内置配置。
    for (const file of [_sha1dbBundledEnvFile(), path.join(this.dataDir, "sha1db.env")]) {
      for (const [key, value] of Object.entries(_loadSha1dbEnvFile(file))) {
        if (!options.env[key]) options.env[key] = value;
      }
    }
    // 秒传池/直连的 CA 证书随包分发：没手动设置就自动指向包内 ca.pem，免配绝对路径。
    // PyInstaller 6 onedir 会把 datas 放进 _internal/，打包态指向 _internal/ca.pem。
    const bundledCa = this.isPackagedSidecar()
      ? path.join(path.dirname(this.sidecarPath()), "_internal", "ca.pem")
      : path.join(__dirname, "..", "backend", "assets", "ca.pem");
    if (fs.existsSync(bundledCa)) {
      if (!options.env.SHA1_POOL_API_CA) options.env.SHA1_POOL_API_CA = bundledCa;
      if (!options.env.SHA1DB_SSL_CA) options.env.SHA1DB_SSL_CA = bundledCa;
    }

    this.child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
    this._log(`[shell] sidecar pid=${this.child.pid}`);
    this.child.stdout.on("data", (chunk) => this._feed(chunk));
    this.child.stderr.on("data", (chunk) => this._feed(chunk));
    this.child.on("exit", (code) => {
      this._log(`[shell] sidecar exited code=${code}`);
      this.child = null;
      if (this.stopping) return;
      if (this.restarts < RESTART_MAX) {
        this.restarts += 1;
        const delay = Math.min(15_000, 1000 * 2 ** (this.restarts - 1));
        this._log(`[shell] restarting in ${Math.round(delay / 1000)}s (attempt ${this.restarts}/${RESTART_MAX})`);
        this.emit("crash", { restarts: this.restarts, delay });
        setTimeout(() => {
          if (!this.stopping) this._spawn();
        }, delay);
      } else {
        this.emit("dead");
      }
    });
  }

  _feed(chunk) {
    this._pendingLine += String(chunk);
    const lines = this._pendingLine.split(/\r?\n/);
    this._pendingLine = lines.pop() || "";
    for (const line of lines) this._log(line);
  }

  waitForHealth(timeoutMs = 60_000) {
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const attempt = () => {
        if (this.stopping) return reject(new Error("backend stopping"));
        const request = http.get(`${this.baseUrl}/api/health`, { timeout: 1500 }, (response) => {
          response.resume();
          if (response.statusCode === 200) return resolve();
          retry();
        });
        request.on("timeout", () => {
          request.destroy();
          retry();
        });
        request.on("error", retry);
      };
      const retry = () => {
        if (Date.now() - started > timeoutMs) return reject(new Error("backend health check timeout"));
        setTimeout(attempt, 300);
      };
      attempt();
    });
  }

  lastLogLines(count = 30) {
    return this.logLines.slice(-count).join("\n");
  }

  async stop() {
    this.stopping = true;
    const child = this.child;
    if (!child) return;
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        try { child.kill("SIGKILL"); } catch (_) { /* already gone */ }
        resolve();
      }, 4000);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      try { child.kill(process.platform === "win32" ? undefined : "SIGTERM"); } catch (_) { /* already gone */ }
    });
    this.child = null;
  }
}

module.exports = { BackendManager };
