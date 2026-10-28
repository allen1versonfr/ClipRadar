import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The app's own server (python src/app.py --browser, or CLIPRADAR_PORT). It only answers requests for
// localhost / 127.0.0.1 (DNS-rebinding guard), so changeOrigin stays off: it sees Host: localhost:5173.
const app = 'http://127.0.0.1:7860'
const toApp = { target: app, changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: '../src/web', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': toApp, '/files': toApp, '/vod': toApp, '/export': toApp,
      '/wmimg': toApp, '/ui': toApp, '/legacy': toApp,
    },
  },
  test: { environment: 'jsdom', setupFiles: ['src/test-setup.ts'] },
})
