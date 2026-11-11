import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ['lucide-react'],
  },
  server: {
    port: 5173,
    // Test output (screenshots, traces) must not trigger full page reloads while the e2e suite runs.
    watch: { ignored: ['**/test-results/**', '**/playwright-report/**', '**/e2e/**'] },
    proxy: {
      '/api': {
        target: 'http://localhost:8087',
        changeOrigin: true,
      },
    },
  },
});
