// Vite builds ONLY the renderer (ui/). electron/main.js and preload.js stay
// unbundled CommonJS — they run in Node, need no transpilation, and keeping
// them plain makes the security-sensitive code easy to audit line by line.
//
// WHY React/Zustand/etc. are devDependencies: Vite inlines them into the
// built bundle (dist/ui). electron-builder only ships "dependencies" inside
// the app package, so keeping UI libs out of that list keeps the installer
// smaller. Only electron-updater runs un-bundled in the main process, so it
// is the one true runtime dependency.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Inject the Content-Security-Policy meta tag per mode, into every HTML entry.
//
// WHY not a static <meta> in the HTML: dev and prod need DIFFERENT policies.
// The production app must be locked down (script-src 'self' only). But in dev,
// @vitejs/plugin-react injects an INLINE <script> for React Fast Refresh, and
// Vite's HMR client opens a websocket — a strict 'self'-only policy blocks
// both, the preamble throws, and the renderer shows nothing (a black screen).
// `ctx.server` is defined only during `vite dev`, so we relax exactly there
// and keep the built HTML strict. This is the standard Vite+CSP pattern.
function cspPlugin() {
  return {
    name: "arthur-csp",
    transformIndexHtml(html, ctx) {
      const dev = ctx.server !== undefined;
      const content = dev
        ? "default-src 'self'; " +
          "script-src 'self' 'unsafe-inline'; " + // Fast Refresh injects an inline preamble; Vite dev is ESM (no eval)
          "style-src 'self' 'unsafe-inline'; " +
          "img-src 'self' data:; " +
          "connect-src 'self' ws://localhost:* ws://127.0.0.1:* http://localhost:* http://127.0.0.1:*; " +
          "object-src 'none'"
        : "default-src 'self'; " +
          "script-src 'self'; " +
          "style-src 'self' 'unsafe-inline'; " + // highlight.js / injected styles
          "img-src 'self' data:; " +
          "connect-src 'self' http://127.0.0.1:*; " + // the local backend only
          "object-src 'none'; base-uri 'none'";
      return {
        html,
        tags: [
          {
            tag: "meta",
            attrs: { "http-equiv": "Content-Security-Policy", content },
            injectTo: "head-prepend",
          },
        ],
      };
    },
  };
}

export default defineConfig({
  root: "ui",
  base: "./", // relative asset paths so file:// loading works inside Electron
  plugins: [react(), cspPlugin()],
  build: {
    outDir: "../dist/ui",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "ui/index.html"),
        quick: resolve(__dirname, "ui/quick.html") // system-tray mini widget
      }
    }
  },
  test: {
    environment: "jsdom",
    // NOTE: paths here are relative to `root` ("ui"), not the repo root
    include: ["src/**/*.test.{js,jsx}"]
  }
});
