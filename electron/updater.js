// Auto-update via electron-updater + GitHub Releases (config in
// electron-builder.yml).
//
// SECURITY NOTE THAT ACTUALLY MATTERS: auto-update is remote code execution
// by design — whoever can serve you an update owns the machine. Two locks:
// (1) electron-updater verifies the download's sha512 against the release
// manifest; (2) Windows code signing ties the manifest to your certificate.
// Ship UNSIGNED auto-updates and any GitHub-account compromise becomes an
// instant botnet. docs/BUILD.md covers certificate options.
const { autoUpdater } = require("electron-updater");

let notify = () => {};

function setupUpdater(onEvent) {
  notify = onEvent;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true; // silent: applies on next quit

  autoUpdater.on("update-available", (info) =>
    notify({ type: "available", version: info.version }));
  autoUpdater.on("update-downloaded", (info) =>
    notify({ type: "ready", version: info.version }));
  autoUpdater.on("error", (err) =>
    notify({ type: "error", message: String(err && err.message).slice(0, 200) }));

  checkNow();
  setInterval(checkNow, 4 * 60 * 60 * 1000); // every 4h while running
}

function checkNow() {
  autoUpdater.checkForUpdates().catch(() => { /* offline is normal */ });
}

module.exports = { setupUpdater, checkNow };
