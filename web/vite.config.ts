import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the SPA on 5173 and proxies the API and media to uvicorn on 8010.
// Production: `npm run build` writes dist/, which serve.py hosts on one port.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8010",
      "/media": "http://127.0.0.1:8010",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
