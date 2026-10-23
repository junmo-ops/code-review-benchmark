import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return;
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react-runtime';
          if (/\/node_modules\/(rc-[^/]+|@rc-component|@ant-design)\//.test(id)) return 'ui-runtime';
        },
      },
    },
  },
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
