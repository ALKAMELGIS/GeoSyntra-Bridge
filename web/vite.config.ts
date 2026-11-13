import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built UI is served by the Python backend from geosyntra_bridge/api/static.
// base "./" keeps every URL relative so the module can be mounted under any platform path.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../geosyntra_bridge/api/static", emptyOutDir: true, chunkSizeWarningLimit: 1600 },
  server: { proxy: { "/api": "http://127.0.0.1:8600" } },
});
