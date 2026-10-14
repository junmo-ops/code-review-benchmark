import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';
export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  {
    files: ['**/*.mjs'],
    languageOptions: { globals: { process: 'readonly', console: 'readonly', URL: 'readonly' } },
    rules: { complexity: ['error', 15] },
  },
  ...tseslint.configs.recommended.map((config) => ({ ...config, files: ['src/**/*.{ts,tsx}'] })),
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': hooks },
    rules: {
      ...hooks.configs.recommended.rules,
      complexity: ['error', 15],
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
);
