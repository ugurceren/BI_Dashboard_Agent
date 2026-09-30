import { createLogger, defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = "http://127.0.0.1:8000"; // uvicorn 127.0.0.1'de dinler; "localhost" önce IPv6 (::1) dener

// Backend kapalıyken (ECONNREFUSED) her istek için yığın izi basmak yerine 30 sn'de bir kısa uyarı.
const logger = createLogger();
const logError = logger.error.bind(logger);
let lastRefused = 0;
logger.error = (msg, opts) => {
  if (msg.includes("http proxy error") && msg.includes("ECONNREFUSED")) {
    const now = Date.now();
    if (now - lastRefused > 30_000) {
      lastRefused = now;
      logger.warn(`Backend'e ulaşılamıyor (${BACKEND}). "BI Agent - Backend" penceresi açık mı? start.bat ile yeniden başlatabilirsiniz.`,
        { timestamp: true });
    }
    return;
  }
  logError(msg, opts);
};

// Ana uygulama: index.html. (viewer.html dev sunucusunda da erişilebilir.)
export default defineConfig({
  customLogger: logger,
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // xfwd: gerçek istemci IP'si backend'e iletilir (Bağlantı Ayarları yalnız bu bilgisayardan / admin)
      "/api": { target: BACKEND, changeOrigin: true, xfwd: true },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
  },
});
