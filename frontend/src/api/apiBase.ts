/**
 * Role: frontend API-origin resolver for bearer-auth feature clients.
 * Used by: HomePage and feature HTTP clients.
 * Key invariants:
 *   - Local dev/test may fall back to the Vite/Compose backend at localhost:8000.
 *   - Production with no VITE_API_URL uses same-origin paths, matching the auth-cookie
 *     deployment model and the nginx CSP.
 *   - Production absolute API URLs must be HTTPS and non-localhost.
 */

const LOCAL_DEV_API_URL = "http://localhost:8000";

function isRelativePath(value: string): boolean {
  return value.startsWith("/");
}

function stripTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, "");
}

function isLocalHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_URL?.trim();
  if (configured === undefined || configured === "") {
    return import.meta.env.PROD ? "" : LOCAL_DEV_API_URL;
  }

  if (isRelativePath(configured)) {
    return stripTrailingSlashes(configured);
  }

  const url = new URL(configured);
  if (import.meta.env.PROD && (url.protocol !== "https:" || isLocalHostname(url.hostname))) {
    throw new Error("Production VITE_API_URL must be HTTPS and must not point to localhost.");
  }
  return stripTrailingSlashes(configured);
}
