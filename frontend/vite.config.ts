import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

declare const process: { env: Record<string, string | undefined> };

const PROXY_TARGET = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";
const proxiedApiPrefixes = ["/auth", "/platform", "/health", "/users", "/admin", "/me"];

function isLocalHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function assertProductionApiUrl(command: string, mode: string): void {
  if (command !== "build" || mode !== "production") {
    return;
  }

  const apiUrl = process.env.VITE_API_URL?.trim();
  if (apiUrl === undefined || apiUrl === "") {
    return; // production default is same-origin API paths
  }
  if (apiUrl.startsWith("/")) {
    return;
  }

  const parsed = new URL(apiUrl);
  if (parsed.protocol !== "https:" || isLocalHostname(parsed.hostname)) {
    throw new Error("Production VITE_API_URL must be HTTPS and must not point to localhost.");
  }
}

export default defineConfig(({ command, mode }) => {
  assertProductionApiUrl(command, mode);

  return {
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
  };
});
