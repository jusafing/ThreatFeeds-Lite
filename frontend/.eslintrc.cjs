/* ESLint config (prompts-021G follow-up).
 *
 * Restores the missing config so `npm run lint` works again. ESLint 8.x
 * legacy format because that is what package.json pins. Plugin set is
 * limited to what is already in devDependencies — no transitive
 * installs required.
 */
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
  ],
  ignorePatterns: ['dist', 'node_modules', '*.cjs', '*.config.ts', '*.config.js'],
  rules: {
    // React Hooks correctness (this is the rule we actually care about).
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
    // Vite fast-refresh boundary check.
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    // Project conventions — be permissive on cosmetics, strict on real bugs.
    '@typescript-eslint/no-unused-vars': [
      'warn',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/no-explicit-any': 'warn',
    '@typescript-eslint/no-empty-function': 'off',
    'no-empty': ['error', { allowEmptyCatch: true }],
  },
}
