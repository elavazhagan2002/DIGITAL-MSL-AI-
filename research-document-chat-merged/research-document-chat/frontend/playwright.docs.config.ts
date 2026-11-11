import { defineConfig } from '@playwright/test';

/**
 * Private config for the Load Documents work (item C): own dev server on 5176 and own backend on 8100,
 * so it does not collide with other runs. Only the Load Documents spec runs here.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: /load-documents\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5176', trace: 'off' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5176 --strictPort',
    url: 'http://127.0.0.1:5176',
    reuseExistingServer: true,
    timeout: 120_000,
    env: { VITE_DEMO_MODE: 'true', VITE_API_BASE_URL: 'http://127.0.0.1:8100' },
  },
});
