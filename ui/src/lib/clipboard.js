// One clipboard path for the whole app.
//
// WHY this file exists: copying was implemented twice, both times straight
// against `navigator.clipboard`, and both broke at once when it turned out the
// Electron main process was denying the clipboard permission (main.js used to
// grant `media` and nothing else). The failure surfaced as a raw DOMException
// string -- "Failed to execute 'write' on 'Clipboard': Write permission
// denied" -- which tells the user nothing they can act on.
//
// THREE PATHS, tried in order, because each fails in a situation the next one
// survives:
//
//   1. Electron's native clipboard, over the preload bridge. No permission, no
//      user-activation requirement, no origin rules. On the desktop app this
//      is the one that always works, so it goes first.
//   2. The async Clipboard API. Used when Arthur runs in a plain browser tab
//      (the Vite dev fallback), where there is no bridge but there IS a secure
//      context. Needs the permission that path 1 does not.
//   3. document.execCommand("copy") over a hidden selection. Deprecated and
//      plain-text only, but it predates the permissions model and works in
//      contexts where the async API is refused outright. Last resort rather
//      than no clipboard at all.
//
// Callers get a boolean and a thrown Error only when ALL paths fail, so a
// button can report honestly without knowing any of this.

/**
 * Put text (and optionally rich HTML) on the clipboard.
 * @returns {Promise<"rich"|"text">} which fidelity actually landed
 */
export async function copyToClipboard({ text, html }) {
  const plain = String(text || "");
  if (!plain && !html) throw new Error("Nothing to copy.");

  // 1. Native (Electron desktop).
  if (window.arthur?.writeClipboard) {
    try {
      const ok = await window.arthur.writeClipboard({ text: plain, html: html || "" });
      if (ok) return html ? "rich" : "text";
    } catch {
      /* bridge missing or main process refused -- fall through */
    }
  }

  // 2. Async Clipboard API (browser, or Electron once the permission is
  //    granted). ClipboardItem is absent on older Chromium, in which case the
  //    rich write is skipped rather than attempted and failed.
  if (html && window.ClipboardItem && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([new window.ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      })]);
      return "rich";
    } catch {
      /* permission denied -- try plain text, which is a lower bar */
    }
  }
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(plain);
      return "text";
    } catch {
      /* fall through to the legacy path */
    }
  }

  // 3. Legacy selection copy.
  if (legacyCopy(plain)) return "text";

  throw new Error("Arthur could not reach the clipboard. Copying is blocked in this window.");
}

function legacyCopy(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // Off-screen rather than display:none -- an unrendered element cannot be
    // selected, and execCommand copies the SELECTION.
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
