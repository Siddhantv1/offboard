import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI app serves the built bundle out of `frontend/dist` and falls back
// to index.html for unknown paths, so client-side routes survive a page reload.
// In dev, Vite serves the UI and proxies /api through to uvicorn, which keeps
// hot reload without a second origin (and so without CORS).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
