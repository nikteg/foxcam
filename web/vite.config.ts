import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Where the Python server runs during `pnpm dev` (override in web/.env.local, e.g.
  // FOXCAM_API=https://cams.example.org to work on the UI against a real server).
  const api = loadEnv(mode, ".", "").FOXCAM_API ?? "http://localhost:8000";
  const target = new URL(api);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(target.hostname);
  return {
    plugins: [
      // `compiler: true` runs the Rust port of React Compiler (oxc-transform-react),
      // so components need no manual useMemo/useCallback.
      react({ compiler: true }),
    ],
    server: {
      port: 5173,
      // The server refuses writes whose Origin is not its own (web/deps.py `_same_origin`).
      // Local server: keep the browser's Host (localhost:5173, or the LAN address), which
      // it accepts. Real server: its own Host (the reverse proxy and TLS route by it) and
      // Origin, so signing in and saving work from the dev UI too.
      proxy: {
        "/api": local
          ? { target: api, changeOrigin: false }
          : { target: api, changeOrigin: true, headers: { origin: target.origin } },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      // Fluent UI alone is ~700 kB minified (185 kB gzip) in its own long-cached chunk.
      chunkSizeWarningLimit: 750,
      rolldownOptions: {
        output: {
          // Long-lived vendor chunks: app deploys don't invalidate the cached Fluent UI bundle.
          codeSplitting: {
            groups: [
              { name: "fluent", test: /node_modules[\\/]@fluentui/ },
              // The recordings player, only loaded where recordings play.
              { name: "player", test: /node_modules[\\/](@vidstack|media-captions|media-icons)/ },
              { name: "vendor", test: /node_modules/ },
            ],
          },
        },
      },
    },
  };
});
