import { defineConfig } from '@playwright/test';

/**
 * Settings (fix D) suite. Runs on its own ports so it never collides with the main suite (5174 / 8098):
 * backend on 8099 (demo mode, throwaway database) and the dev server on 5175, started separately.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: /settings\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5175', trace: 'off', screenshot: 'only-on-failure' },
});
