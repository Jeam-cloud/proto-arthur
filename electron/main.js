// Electron main process — window management, IPC, and the security posture.
//
// Renderer hardening (every flag deliberate):
//   contextIsolation: true  — renderer JS cannot touch preload internals
//   sandbox: true           — renderer runs in the OS sandbox, no Node
//   nodeIntegration: false  — belt to sandbox's suspenders
// plus: navigation is pinned to our own UI, window.open is denied (external
// links go to the system browser), permission requests are denied except the
// microphone (voice input), and every IPC handler validates its sender frame.
const {
  app, BrowserWindow, ipcMain, dialog, shell, globalShortcut, session, clipboard,
} = require("electron");
const path = require("path");
const { BackendManager } = require("./backend");
const { createTray } = require("./tray");
const { setupUpdater } = require("./updater");

const DEV = !!process.env.ARTHUR_DEV;
const DEV_URL = "http://localhost:5173";

let mainWindow = null;
let quickWindow = null;
let tray = null;
const backend = new BackendManager();

// single instance — a second launch focuses the existing window instead
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => showMain());

  app.whenReady().then(async () => {
    hardenSession();
    registerIpc();
    createMainWindow();
    tray = createTray({ onOpen: showMain, onQuit: () => quitApp() });
    registerHotkey();
    if (!DEV) setupUpdater((payload) => sendToMain("updates:event", payload));
    backend.onStateChange((state) => sendToMain("backend:state", state));
    await backend.start();
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0d0d0d",
    show: false, // avoid the white flash; show on ready-to-show
    autoHideMenuBar: true,
    icon: path.join(__dirname, "..", "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  loadUI(mainWindow, "index.html");
  pinNavigation(mainWindow);

  // Dev only: log hard load failures to the terminal. DevTools no longer
  // auto-opens (it was crowding every boot) — hit Ctrl+Shift+I when you
  // actually want it.
  if (DEV) {
    mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) =>
      console.error(`renderer failed to load: ${code} ${desc} ${url}`));
  }

  // Close button minimizes to tray (assistant apps live in the tray);
  // real quit comes from the tray menu or Ctrl+Q.
  mainWindow.on("close", (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createQuickWindow() {
  // Wide-bar rectangle (Spotlight-style) — a landscape strip, not a tall
  // panel. The answer area scrolls when a reply outgrows it.
  quickWindow = new BrowserWindow({
    width: 680,
    height: 340,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    backgroundColor: "#0d0d0d",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  loadUI(quickWindow, "quick.html");
  pinNavigation(quickWindow);
  quickWindow.on("blur", () => quickWindow.hide());
}

function loadUI(win, page) {
  if (DEV) win.loadURL(`${DEV_URL}/${page}`);
  else win.loadFile(path.join(__dirname, "..", "dist", "ui", page));
}

function pinNavigation(win) {
  // The renderer displays OUR app, never remote content. Any navigation away
  // (e.g. an injected link) is cancelled; external URLs open in the browser.
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(DEV_URL) && !url.startsWith("file://")) {
      e.preventDefault();
      if (url.startsWith("https://")) shell.openExternal(url);
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });
}

// Permissions the renderer is allowed to have. Deny-by-default still: this is
// an allowlist, not a switch to "ask the user".
//
//   media                      -> the microphone, for voice input.
//   clipboard-sanitized-write  -> Copy buttons.
//
// WHY clipboard had to be added: the handler used to be `cb(permission ===
// "media")`, which denied EVERY other permission including clipboard writes,
// so every Copy button in the app failed with "Failed to execute 'write' on
// 'Clipboard': Write permission denied". The renderer runs our own UI from
// file:// with no remote content (see pinNavigation), so the page asking to
// write to the clipboard is always our page.
//
// SANITIZED, not raw: `clipboard-sanitized-write` lets the renderer put plain
// text and HTML on the clipboard with Chromium sanitising the payload first.
// `clipboard-read` is deliberately NOT here -- nothing in Arthur needs to read
// what the user copied from other applications, and granting it would let a
// prompt-injected page exfiltrate whatever is on their clipboard.
const ALLOWED_PERMISSIONS = new Set(["media", "clipboard-sanitized-write"]);

function hardenSession() {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(ALLOWED_PERMISSIONS.has(permission));
  });
  // Chromium checks some permissions through this synchronous path instead of
  // the request handler above, and clipboard writes are one of them. Without
  // it the grant above is silently ignored for exactly the case it was added
  // for.
  session.defaultSession.setPermissionCheckHandler((_wc, permission) =>
    ALLOWED_PERMISSIONS.has(permission));
}

function registerHotkey() {
  // Global summon — tracker p2t20. Toggles the mini widget from anywhere.
  const ok = globalShortcut.register("CommandOrControl+Shift+A", toggleQuick);
  if (!ok) console.warn("global hotkey unavailable (already taken by another app)");
}

function toggleQuick() {
  if (!quickWindow || quickWindow.isDestroyed()) createQuickWindow();
  if (quickWindow.isVisible()) quickWindow.hide();
  else { quickWindow.show(); quickWindow.focus(); }
}

function showMain() {
  if (!mainWindow || mainWindow.isDestroyed()) createMainWindow();
  mainWindow.show();
  mainWindow.focus();
}

function sendToMain(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function quitApp() {
  app.isQuitting = true;
  app.quit();
}

// ---- IPC (all handlers validate the sender is one of OUR windows) ----
function fromOurWindow(event) {
  const wc = event.senderFrame ? event.sender : null;
  return wc && [mainWindow, quickWindow].some(
    (w) => w && !w.isDestroyed() && w.webContents.id === wc.id
  );
}

function registerIpc() {
  const handle = (channel, fn) =>
    ipcMain.handle(channel, (event, ...args) => {
      if (!fromOurWindow(event)) throw new Error("unauthorized IPC sender");
      return fn(event, ...args);
    });

  handle("backend:info", () => backend.info());
  handle("app:version", () => app.getVersion());

  // WRITE-ONLY clipboard, on purpose.
  //
  // The web Clipboard API needs a permission grant AND transient user
  // activation, and it silently varies by Chromium version and origin -- which
  // is how every Copy button in the app ended up broken at once. The native
  // module has neither constraint, so on the desktop this is the reliable
  // path and the web API is only the fallback (see ui/src/lib/clipboard.js).
  //
  // There is deliberately no clipboard:read counterpart. Reading would let
  // any injected content in the renderer lift whatever the user last copied
  // from another application -- passwords included -- and nothing in Arthur
  // needs it.
  handle("clipboard:write", (_e, payload) => {
    const text = typeof payload?.text === "string" ? payload.text : "";
    const html = typeof payload?.html === "string" ? payload.html : "";
    if (!text && !html) return false;
    // Both formats in one write: the receiving app picks the richest it
    // understands, so this pastes as a formatted document into Word and as
    // clean text into a plain editor.
    clipboard.write(html ? { text, html } : { text });
    return true;
  });
  handle("window:showMain", () => showMain());
  handle("window:hideQuick", () => quickWindow && quickWindow.hide());

  handle("dialog:pickFiles", async () => {
    // multiSelections only — NOT openDirectory. A folder is attached by
    // dragging it in; offering it here would mean one dialog that sometimes
    // returns files and sometimes a directory, which the caller then has to
    // disambiguate for no benefit.
    const res = await dialog.showOpenDialog(mainWindow, {
      properties: ["openFile", "multiSelections"],
    });
    return res.canceled ? [] : res.filePaths;
  });

  handle("dialog:pickFolder", async () => {
    const res = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] });
    return res.canceled ? null : res.filePaths[0];
  });

  handle("shell:openExternal", (_e, url) => {
    // https only — never let the renderer launch file:// or custom protocols
    if (typeof url === "string" && url.startsWith("https://")) shell.openExternal(url);
  });

  handle("updates:check", () => {
    if (!DEV) require("./updater").checkNow();
  });
}

app.on("before-quit", () => { app.isQuitting = true; });
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  backend.stop();
});
app.on("window-all-closed", (e) => e.preventDefault()); // tray app: stay alive
