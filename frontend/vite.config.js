import { resolve } from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Multi-page build: index.html (routes/map/forum) and discussion.html
// (the general Q&A board) are genuinely separate pages -- separate URL,
// separate JS bundle -- not two views of one SPA. Vite's native
// multi-entry build handles this without a second template engine or
// framework; each page still renders with React, just via its own
// entry script (src/main.jsx vs src/discussion-main.jsx).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Phone testing over an HTTPS tunnel (cloudflared quick tunnel):
    // start a second dev server with VITE_API_URL=/api so the page and
    // API share one origin (no CORS, first-party cookie), and allow the
    // tunnel's random hostname. Dev server only; production builds are
    // unaffected.
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        discussion: resolve(__dirname, "discussion.html"),
      },
    },
  },
});