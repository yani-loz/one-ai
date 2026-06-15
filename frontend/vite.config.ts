import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

// Node's `process` in the config's build-time context (no @types/node dependency needed).
declare const process: { env: Record<string, string | undefined> };

// The auth endpoints must be SAME-ORIGIN with the SPA so the httpOnly refresh cookie
// (Control C) flows: over dev http a cross-origin cookie is impossible (SameSite=None needs
// Secure). This proxy forwards the API prefixes to the backend so the browser sees one origin
// (:5173). Target is the compose service name inside Docker, localhost for a local `pnpm dev`.
const PROXY_TARGET = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";
const proxiedApiPrefixes = ["/auth", "/platform", "/health"];

// host:true + usePolling are required for hot-reload inside the Docker bind mount
// on Windows, where native filesystem events do not cross into the container.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    watch: { usePolling: true },
    proxy: Object.fromEntries(
      proxiedApiPrefixes.map((prefix) => [prefix, { target: PROXY_TARGET, changeOrigin: true }]),
    ),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/vite-env.d.ts", "src/test/**"],
      thresholds: { lines: 70, functions: 70, branches: 70, statements: 70 },
    },
  },
});
