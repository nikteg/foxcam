import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Where the Python server runs during `pnpm dev` (override in web/.env.local).
  const api = loadEnv(mode, ".", "").FOXCAM_API ?? "http://localhost:8000";
  return {
    plugins: [
      // `compiler: true` runs the Rust port of React Compiler (oxc-transform-react),
      // so components need no manual useMemo/useCallback.
      react({ compiler: true }),
    ],
    server: {
      port: 5173,
      proxy: { "/api": api },
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
