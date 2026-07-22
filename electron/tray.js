// System tray — tracker p3t16. The app "lives" here between uses.
const { Tray, Menu, nativeImage } = require("electron");
const path = require("path");

function createTray({ onOpen, onQuit }) {
  // 16x16 template icon; build/icon.ico is used for the installer/taskbar
  const icon = nativeImage.createFromPath(path.join(__dirname, "..", "build", "tray.png"));
  const tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip("Arthur — local AI assistant");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Arthur", click: onOpen },
    { label: "Quick widget  (Ctrl+Shift+A)", click: () => { /* hotkey hint */ } },
    { type: "separator" },
    { label: "Quit Arthur", click: onQuit },
  ]));
  tray.on("double-click", onOpen);
  return tray;
}

module.exports = { createTray };
