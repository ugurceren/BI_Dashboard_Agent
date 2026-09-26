import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// Bağımsız görüntüleyici: tek dosyalık dist-viewer/viewer.html (HTML dışa aktarma şablonu).
export default defineConfig({
  plugins: [react(), viteSingleFile({ removeViteModuleLoader: true })],
  build: {
    outDir: "dist-viewer",
    emptyOutDir: true,
    chunkSizeWarningLimit: 3000,
    rollupOptions: {
      input: "viewer.html",
    },
  },
});
