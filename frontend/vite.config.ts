import { defineConfig } from 'vite'

export default defineConfig({
  base: '/design/',
  root: 'original',
  publicDir: false,
  build: {
    outDir: '../../web/creator/design',
    emptyOutDir: true,
    target: 'es2022',
    assetsDir: 'assets',
    sourcemap: false,
  },
  server: {
    proxy: {
      '/app-api': 'http://127.0.0.1:4180',
      '/api': 'http://127.0.0.1:4180',
    },
  },
})
