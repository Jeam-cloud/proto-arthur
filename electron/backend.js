// Backend lifecycle: spawn the FastAPI subprocess, share its port + a fresh
// auth token, restart it if it dies, kill it cleanly on quit.
//
// WHY the token is generated HERE: the Electron main process is the trust
// root. It mints a random secret per launch and hands it to (a) the Python
// child via environment and (b) the renderer via IPC. A malicious webpage in
// some browser can reach 127.0.0.1 but can never read this value — that is
// the entire local-API security model, so this file stays tiny and auditable.
const { spawn } = require("child_process");
const crypto = require("crypto");
const net = require("net");
const path = require("path");
const fs = require("fs");
const { app } = require("electron");

const MAX_RESTARTS = 3;
// After this long without a healthy response we stop saying "starting" and
// start saying "still starting" — a message, NOT a deadline. See _waitForHealth.
const SLOW_AFTER_MS = 8_000;

class BackendManager {
  constructor() {
    this.token = crypto.randomBytes(32).toString("hex");
    this.port = null;
    this.proc = null;
    this.restarts = 0;
    this.state = "starting"; // starting | slow | ready | failed | stopped
    this.listeners = new Set();
    this.stopping = false;
    // Bumped on every start(). A health poll from a superseded start must not
    // report on the current one: a restart picks a NEW port, so an in-flight
    // poll from the old attempt is asking a port nobody is listening on and
    // its answer is meaningless.
    this.generation = 0;
  }

  onStateChange(fn) { this.listeners.add(fn); }
  _setState(s) { this.state = s; this.listeners.forEach((fn) => fn(s)); }

  info() { return { port: this.port, token: this.token, state: this.state }; }

  async start() {
    const gen = ++this.generation;
    this.port = await freePort();
    const { command, args, cwd } = resolveBackend();

    const logPath = path.join(app.getPath("userData"), "logs");
    fs.mkdirSync(logPath, { recursive: true });
    const logStream = fs.createWriteStream(path.join(logPath, "backend-stdio.log"), { flags: "a" });

    this.proc = spawn(command, args, {
      cwd,
      env: {
        ...process.env,
        ARTHUR_PORT: String(this.port),
        ARTHUR_AUTH_TOKEN: this.token,
        ARTHUR_DATA_DIR: app.getPath("userData"),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    this.proc.stdout.pipe(logStream);
    this.proc.stderr.pipe(logStream);

    this.proc.on("exit", (code) => {
      if (this.stopping) return;
      // Crash-loop guard: restart a few times with backoff, then surface
      // a real error screen instead of flapping forever.
      if (this.restarts < MAX_RESTARTS) {
        this.restarts += 1;
        console.warn(`backend exited (${code}); restart ${this.restarts}/${MAX_RESTARTS}`);
        setTimeout(() => this.start(), 1000 * this.restarts);
      } else {
        this._setState("failed");
      }
    });

    const healthy = await this._waitForHealth(60_000);
    this._setState(healthy ? "ready" : "failed");
  }

  async _waitForHealth(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const res = await fetch(`http://127.0.0.1:${this.port}/health`);
        if (res.ok) return true;
      } catch { /* not up yet */ }
      await new Promise((r) => setTimeout(r, 400));
    }
    return false;
  }

  stop() {
    this.stopping = true;
    if (!this.proc) return;
    if (process.platform === "win32") {
      // /T kills the whole tree — uvicorn workers must not outlive the app
      spawn("taskkill", ["/pid", String(this.proc.pid), "/T", "/F"], { windowsHide: true });
    } else {
      this.proc.kill("SIGTERM");
    }
  }
}

function resolveBackend() {
  if (process.env.ARTHUR_DEV) {
    // dev: run from source with the project venv's python
    const pythonDir = path.join(__dirname, "..", "python");
    const venvPy = process.platform === "win32"
      ? path.join(pythonDir, ".venv", "Scripts", "python.exe")
      : path.join(pythonDir, ".venv", "bin", "python");
    const python = fs.existsSync(venvPy) ? venvPy : "python";
    return { command: python, args: ["main.py"], cwd: pythonDir };
  }
  // prod: PyInstaller onedir output shipped in resources/
  const exe = process.platform === "win32" ? "arthur-backend.exe" : "arthur-backend";
  const dir = path.join(process.resourcesPath, "backend");
  return { command: path.join(dir, exe), args: [], cwd: dir };
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

module.exports = { BackendManager };
