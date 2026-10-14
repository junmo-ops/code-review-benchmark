import js from "../frontend/node_modules/@eslint/js/src/index.js";
export default [
  js.configs.recommended,
  { files: ["**/e2e/*.mjs"], languageOptions: { globals: { localStorage: "readonly", document: "readonly", performance: "readonly" } } },
  {
    files: ["**/*.mjs"],
    languageOptions: {
      globals: { process: "readonly", console: "readonly", URL: "readonly" },
    },
    rules: { complexity: ["error", 15] },
  },
];
