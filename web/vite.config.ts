import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `npm run dev` proxies the API to `explain-beaucoup web --no-browser`;
// `npm run build` writes the client into the Python package, where the
// server serves it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    outDir: "../explain_beaucoup/web/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
});
