/* Dev-mode orchestrator: backend (venv) + vite dev server + electron.
   All children are killed when this script exits. */

const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const repoRoot = path.join(__dirname, "..");
const backendDir = path.join(repoRoot, "backend");
const webDir = path.join(repoRoot, "web");
const BACKEND_PORT = 8321;
const VITE_PORT = 5174;
const children = [];

function pythonBin() {
  return process.platform === "win32"
    ? path.join(backendDir, ".venv", "Scripts", "python.exe")
    : path.join(backendDir, ".venv", "bin", "python");
}

function waitFor(url, timeoutMs, label) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(url, { timeout: 1200 }, (response) => {
        response.resume();
        if (response.statusCode < 500) return resolve();
        retry();
      });
      request.on("timeout", () => { request.destroy(); retry(); });
      request.on("error", retry);
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) return reject(new Error(`${label} not ready`));
      setTimeout(attempt, 300);
    };
    attempt();
  });
}

function run(name, command, args, options) {
  const child = spawn(command, args, { stdio: "inherit", ...options });
  children.push(child);
  child.on("exit", (code) => {
    if (code && code !== 0 && !shuttingDown) {
      console.error(`[${name}] exited with code ${code}`);
    }
  });
  return child;
}

let shuttingDown = false;
function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    try { child.kill("SIGTERM"); } catch (_) { /* gone */ }
  }
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

(async () => {
  const python = pythonBin();
  if (!fs.existsSync(python)) {
    console.error("Dev python missing — run: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt");
    process.exit(1);
  }
  if (!fs.existsSync(path.join(webDir, "node_modules"))) {
    console.log("[dev] installing admin-web dependencies...");
    execSync("npm install", { cwd: webDir, stdio: "inherit" });
  }

  run("backend", python, ["-m", "app"], {
    cwd: backendDir,
    env: { ...process.env, PYTHONPATH: backendDir, CLOUD123_PORT: String(BACKEND_PORT) },
  });
  await waitFor(`http://127.0.0.1:${BACKEND_PORT}/api/health`, 60_000, "backend");
  console.log("[dev] backend ready on", BACKEND_PORT);

  run("vite", "npm", ["run", "dev", "--", "--port", String(VITE_PORT), "--strictPort"], {
    cwd: webDir,
    env: { ...process.env, VITE_API_TARGET: `http://127.0.0.1:${BACKEND_PORT}` },
  });
  await waitFor(`http://127.0.0.1:${VITE_PORT}/`, 60_000, "vite");
  console.log("[dev] vite ready on", VITE_PORT);

  run("electron", "npx", ["electron", "."], {
    cwd: path.join(__dirname, ".."),
    env: {
      ...process.env,
      CLOUD123_DEV_URL: `http://127.0.0.1:${VITE_PORT}/admin`,
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
  });
})().catch((error) => {
  console.error(error);
  shutdown();
});
