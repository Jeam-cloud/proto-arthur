// Minimal flat config: catch real bugs (undefined vars, unused imports),
// stay out of the way on style — formatting opinions belong to your editor.
const js = require("@eslint/js");

module.exports = [
  js.configs.recommended,
  {
    files: ["electron/**/*.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { require: "readonly", module: "readonly", process: "readonly", __dirname: "readonly", console: "readonly", setTimeout: "readonly", clearTimeout: "readonly", setInterval: "readonly", clearInterval: "readonly", Buffer: "readonly" }
    }
  },
  {
    files: ["ui/src/**/*.{js,jsx}"],
    languageOptions: {
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { window: "readonly", document: "readonly", fetch: "readonly", console: "readonly", navigator: "readonly", MediaRecorder: "readonly", Blob: "readonly", FormData: "readonly", AbortController: "readonly", TextDecoder: "readonly", URL: "readonly", localStorage: "readonly", requestAnimationFrame: "readonly", setTimeout: "readonly", clearTimeout: "readonly", setInterval: "readonly", clearInterval: "readonly", crypto: "readonly" }
    },
    rules: { "no-unused-vars": ["warn", { varsIgnorePattern: "^_" }] }
  }
];
