import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the SPA on 5173 and proxies the API and media to uvicorn on 8010.
// `--mode verify`: the same on 5174 against a second server on 8012, so a change can be tried
// in a browser without rebuilding the dist/ the live server on 8010 is handing out.
// Production: `npm run build` writes dist/, which serve.py hosts on one port.
export default defineConfig(({ mode }) => {
  const verify = mode === "verify";
  const api = `http://127.0.0.1:${verify ? 8012 : 8010}`;
  return {
    plugins: [react()],
    server: {
      port: verify ? 5174 : 5173,
      strictPort: verify,
      proxy: {
        "/api": api,
        "/media": api,
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
