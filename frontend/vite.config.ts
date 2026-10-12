import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    // Obtain the backend's CSRF token without putting credentials in frontend configuration.
    proxy: {
      '/api': {
        target: process.env.BENCH_API_UPSTREAM || 'http://127.0.0.1:8765',
        changeOrigin: true,
        headers: {
          Origin: new URL(process.env.BENCH_API_UPSTREAM || 'http://127.0.0.1:8765').origin,
        },
      },
    },
  },
});
