import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

declare const process: { cwd(): string; env: Record<string, string | undefined> }

export default defineConfig(({ mode }) => {
  // Pull VITE_BACKEND_URL from .env files AND from process.env (start.py sets it).
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }
  const backend = env.VITE_BACKEND_URL || 'http://localhost:8500'
  const wsBackend = backend.replace(/^http/, 'ws')

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/ws': { target: wsBackend, ws: true, changeOrigin: true },
      },
    },
  }
})
