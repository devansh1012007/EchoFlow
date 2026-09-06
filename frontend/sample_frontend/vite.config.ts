import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    exclude: ['node_modules'],
  },
  server: {
    port: 3021,
    proxy: {
      '/auth': { target: 'http://localhost:8000', changeOrigin: true },
      '/feed': { target: 'http://localhost:8000', changeOrigin: true },
      '/suggestions': { target: 'http://localhost:8000', changeOrigin: true },
      '/tags': { target: 'http://localhost:8000', changeOrigin: true },
      '/clips': { target: 'http://localhost:8000', changeOrigin: true },
      '/interactions': { target: 'http://localhost:8000', changeOrigin: true },
      '/comments': { target: 'http://localhost:8000', changeOrigin: true },
      '/share': { target: 'http://localhost:8000', changeOrigin: true },
      '/follow': { target: 'http://localhost:8000', changeOrigin: true },
      '/profile': { target: 'http://localhost:8000', changeOrigin: true },
      '/media': { target: 'http://localhost:8000', changeOrigin: true },
      '/subscription': { target: 'http://localhost:8000', changeOrigin: true },
      '/webhooks': { target: 'http://localhost:8000', changeOrigin: true }
    }
  }
})
