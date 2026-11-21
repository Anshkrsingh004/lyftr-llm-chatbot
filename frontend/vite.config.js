import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The frontend talks to the backend at VITE_API_BASE_URL (baked in at build time).
export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 3000 },
  preview: { host: true, port: 3000 },
})
