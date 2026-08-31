import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend URL the dev/preview server proxies /api (and, from Phase 5,
// /ws) to. Bare metal: 127.0.0.1:8123. docker-compose sets VITE_API_PROXY to
// the backend service name.
const target = process.env.VITE_API_PROXY || 'http://127.0.0.1:8123'
const proxy = {
  '/api': target,
  '/ws': { target, ws: true },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
})
