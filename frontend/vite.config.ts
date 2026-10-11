import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server (npm run dev, http://localhost:5173) forwards API calls to the Python app on :8000.
// In normal use the Python app serves the built files from dist/ itself.
const backend = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  // Modern browsers only, so CSS like backdrop-filter is emitted unprefixed
  build: { cssTarget: ['chrome120', 'edge120', 'safari18', 'firefox128'] },
  server: {
    proxy: {
      '/api': backend,
      '/auth': backend,
      '/attachments': backend,
    },
  },
})
