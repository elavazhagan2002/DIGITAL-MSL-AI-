import { defineConfig } from '@playwright/test';

/**
 * The backend is started separately on 8098 (demo mode, throwaway database, empty approved-repository list).
 * The dev server is started here on 5174 with demo mode on, pointing at that backend.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5174', trace: 'off' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort',
    url: 'http://127.0.0.1:5174',
    reuseExistingServer: true,
    timeout: 120_000,
    env: { VITE_DEMO_MODE: 'true', VITE_API_BASE_URL: 'http://127.0.0.1:8098' },
  },
});
