// Every copy button in the app goes through this file, and it only runs on a
// path where something has already gone wrong somewhere else -- which is
// exactly the code that never gets exercised by hand. The fallback ORDER is
// the thing worth pinning: it is what turned "Write permission denied" from a
// dead end into a working copy.
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "./clipboard";

// jsdom is not configured for this project, so the few globals the helper
// touches are stubbed by hand. vi.stubGlobal rather than assignment: in
// current Node `globalThis.navigator` is a getter-only property and plain
// assignment throws "Cannot set property navigator".
//
// Keeping this explicit also documents the helper's entire global surface.
function setup({ arthur, clipboard, ClipboardItem, execCommand } = {}) {
  vi.stubGlobal("window", { arthur, ClipboardItem });
  vi.stubGlobal("navigator", { clipboard });
  vi.stubGlobal("document", {
    execCommand,
    createElement: () => ({
      setAttribute() {}, select() {}, setSelectionRange() {}, style: {},
    }),
    body: { appendChild() {}, removeChild() {} },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("copyToClipboard", () => {
  it("prefers the native bridge when Electron provides one", async () => {
    const writeClipboard = vi.fn().mockResolvedValue(true);
    setup({
      arthur: { writeClipboard },
      // A web API that would THROW if reached, proving it was not.
      clipboard: { writeText: () => { throw new Error("should not be called"); } },
    });

    expect(await copyToClipboard({ text: "hi", html: "<p>hi</p>" })).toBe("rich");
    expect(writeClipboard).toHaveBeenCalledWith({ text: "hi", html: "<p>hi</p>" });
  });

  it("falls through to the web API when the bridge refuses", async () => {
    const writeText = vi.fn().mockResolvedValue();
    setup({
      arthur: { writeClipboard: vi.fn().mockRejectedValue(new Error("denied")) },
      clipboard: { writeText },
    });

    expect(await copyToClipboard({ text: "hi" })).toBe("text");
    expect(writeText).toHaveBeenCalledWith("hi");
  });

  it("drops from rich to plain rather than failing the copy", async () => {
    // THE REPORTED BUG shape: main.js denied the clipboard permission, so the
    // rich write rejected. Losing the formatting is a far smaller failure than
    // losing the copy, so it must not stop there.
    const writeText = vi.fn().mockResolvedValue();
    setup({
      ClipboardItem: class {},
      clipboard: {
        write: vi.fn().mockRejectedValue(new Error("Write permission denied")),
        writeText,
      },
    });

    expect(await copyToClipboard({ text: "hi", html: "<p>hi</p>" })).toBe("text");
    expect(writeText).toHaveBeenCalled();
  });

  it("uses the legacy selection copy when every modern path is refused", async () => {
    const execCommand = vi.fn().mockReturnValue(true);
    setup({
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("nope")) },
      execCommand,
    });

    expect(await copyToClipboard({ text: "hi" })).toBe("text");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });

  it("throws something a user can act on when nothing works", async () => {
    setup({ execCommand: () => false });
    await expect(copyToClipboard({ text: "hi" })).rejects.toThrow(/could not reach the clipboard/i);
  });

  it("refuses an empty copy instead of silently succeeding", async () => {
    setup({});
    await expect(copyToClipboard({ text: "" })).rejects.toThrow(/nothing to copy/i);
  });
});
