import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on citi-api (FastAPI, port 8000); /api is proxied so the browser sees one origin.
const api = process.env.CITI_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
});
