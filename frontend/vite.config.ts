import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist', emptyOutDir: true,
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          const name = id.replaceAll('\\', '/').match(/\/node_modules\/((?:@[^/]+\/)?[^/]+)/)?.[1];
          if (!name) return;
          if (['react', 'react-dom', 'scheduler', 'react-router', 'react-router-dom'].includes(name)) return 'react-vendor';
          if (name.startsWith('@ant-design/icons')) return 'icons';
          if (name.startsWith('@ant-design/')) return 'antd-support';
          if (name.startsWith('rc-') || name.startsWith('@rc-component/')) return 'rc-components';
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
