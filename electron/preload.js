// Preload: the ONLY bridge between the sandboxed renderer and the OS.
//
// Rules this file lives by (Electron security checklist, made concrete):
//  * contextBridge only — nothing is attached to window directly.
//  * No generic `invoke(channel, ...)` passthrough. Every capability is a
//    named function with a fixed channel, so the renderer's maximum power is
//    enumerable by reading this one file.
//  * No Node objects cross the bridge — plain JSON in, plain JSON out.
const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("arthur", {
  // backend connection info: { port, token, state }
  getBackendInfo: () => ipcRenderer.invoke("backend:info"),
  onBackendState: (cb) => {
    const listener = (_e, state) => cb(state);
    ipcRenderer.on("backend:state", listener);
    return () => ipcRenderer.removeListener("backend:state", listener);
  },

  // native helpers
  // Write-only by design -- there is no readClipboard counterpart, and there
  // should not be. See the clipboard:write handler in main.js.
  writeClipboard: ({ text, html } = {}) =>
    ipcRenderer.invoke("clipboard:write", { text, html }),
  pickFolder: () => ipcRenderer.invoke("dialog:pickFolder"),
  pickFiles: () => ipcRenderer.invoke("dialog:pickFiles"),

  // The real path of a dropped File.
  //
  // WHY this needs a bridge at all: Electron REMOVED the non-standard
  // `File.path` property (32.0), so a renderer holding a dropped File can no
  // longer tell where it came from. `webUtils.getPathForFile` is the sanctioned
  // replacement and is only available in the preload.
  //
  // WHY it matters here: reading the path lets the backend open the file
  // directly, instead of the renderer slurping a 20MB PDF into memory and
  // pushing it back out over HTTP. It is also the ONLY way to detect that the
  // user dropped a FOLDER -- a directory arrives as a File with no usable
  // contents, and its path is the only signal.
  //
  // Returns "" rather than throwing for anything that is not a real file (a
  // dragged image from a web page, for instance), so callers can fall back to
  // uploading bytes.
  pathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file) || "";
    } catch {
      return "";
    }
  },
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
  getVersion: () => ipcRenderer.invoke("app:version"),
  platform: process.platform,

  // updates
  checkForUpdates: () => ipcRenderer.invoke("updates:check"),
  onUpdateEvent: (cb) => {
    const listener = (_e, payload) => cb(payload);
    ipcRenderer.on("updates:event", listener);
    return () => ipcRenderer.removeListener("updates:event", listener);
  },

  // quick widget <-> main window
  openMainWindow: () => ipcRenderer.invoke("window:showMain"),
  hideQuickWidget: () => ipcRenderer.invoke("window:hideQuick"),
});
