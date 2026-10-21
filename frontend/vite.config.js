import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes the app to ./dist (frontend/dist), which the Python server serves.
// `npm run dev` proxies API calls to a server running on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000", "/public": "http://127.0.0.1:8000" } },
});
