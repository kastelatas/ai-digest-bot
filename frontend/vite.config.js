import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// dev: `npm run dev` проксирует /api на локально запущенный бэкенд (uvicorn на :8080)
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8080' } },
});
