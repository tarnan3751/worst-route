// The dev server and the build (docs/reference/frontend.md). The API has no /api prefix, so the
// proxy strips it; WORST_ROUTE_API points it at a server other than the default port (a lane's
// own, for instance). `vite preview` proxies the same way.
import { defineConfig } from "vite";

const api = process.env.WORST_ROUTE_API ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": {
    target: api,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  server: { proxy },
  preview: { proxy },
  // MapLibre alone is about 1 MB minified (280 kB gzipped); one chunk is fine here.
  build: { chunkSizeWarningLimit: 1500 },
});
