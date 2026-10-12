import js from "../frontend/node_modules/@eslint/js/src/index.js";
export default [
  js.configs.recommended,
  {
    files: ["**/*.mjs"],
    languageOptions: {
      globals: { process: "readonly", console: "readonly", URL: "readonly" },
    },
    rules: { complexity: ["error", 15] },
  },
];
