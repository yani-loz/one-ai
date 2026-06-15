/**
 * Role: Thin HTTP client for the Identity backend — owns token state (access token in
 *       memory; the COMPANY refresh token in an httpOnly cookie the JS never sees), and
 *       login/refresh/logout/me plus a fetch wrapper that attaches the Bearer token and
 *       transparently refreshes once on 401.
 * Used by: AuthProvider.tsx (login/refresh/logout/me). `authorizedFetch` +
 *   `AuthRequestError` are also re-exported via ./index for sibling modules (admin/connect/
 *   platform/support) to make authenticated calls without re-implementing token attachment.
 * Depends on: ./types. Auth endpoints are reached SAME-ORIGIN (relative paths) — see AUTH_BASE.
 * Key invariants:
 *   - The COMPANY refresh token is an httpOnly cookie (Control C): set by /auth/login, sent
 *     automatically by the browser on /auth/refresh + /auth/logout, never readable by JS, so
 *     an injected script cannot exfiltrate it. The access token lives in memory only (also
 *     never localStorage). NOTHING auth-related is persisted in JS-readable storage anymore.
 *   - The high-privilege PLATFORM refresh token is kept IN MEMORY ONLY (AUD-14) — body-based
 *     /platform/refresh + /platform/logout — so it is gone on reload and never persisted.
 *   - Auth uses RELATIVE URLs (AUTH_BASE = "") so the httpOnly cookie is same-origin: in dev
 *     the Vite proxy forwards /auth + /platform to the backend; in prod the API is served
 *     behind the same origin / ingress. The Bearer-only feature clients keep using
 *     VITE_API_URL — only AUTH must be same-origin for the cookie to flow.
 *   - `performRefresh` / `logout` are domain-aware: a platform session rotates/revokes via
 *     /platform/*; a company session via /auth/* (cookie).
 *   - Company refresh is serialized ACROSS TABS (Web Locks API) so two tabs never race the
 *     single-use rotation; the rotated httpOnly cookie is shared by the browser across tabs.
 *     Platform refresh is in-memory (single-tab) and needs no lock.
 *   - `authorizedFetch` retries a 401 at most ONCE, and never tries to refresh the
 *     /auth/refresh or /auth/login calls themselves (no infinite loop).
 *   - Every authorized request is TIME-BOUNDED (30s AbortSignal.timeout unless the caller
 *     passes its own signal): drawers block closing mid-submit, so an unsettled fetch on a
 *     blackholed network must never wedge the UI until the browser's own timeout.
 *   - A failed refresh clears the in-memory session so callers fall back to /login.
 */
import type {
  AccessTokenResponse,
  AuthUser,
  CompanyLoginResponse,
  PlatformAdminView,
  TokenPair,
} from "./types";

// Auth endpoints are SAME-ORIGIN (relative): the company refresh token is an httpOnly cookie
// the browser only sends back to its own origin. Dev serves /auth + /platform through the Vite
// proxy; prod serves the API behind the same origin / ingress.
const AUTH_BASE = "";

/** Access token lives only in module memory — gone on hard refresh by design. */
let accessTokenInMemory: string | null = null;

/**
 * Platform refresh token — kept in MEMORY ONLY (never persisted, AUD-14) so the
 * high-privilege platform session can refresh/log out within a tab without exposing the
 * credential to XSS. Null for a company session (whose refresh lives in the httpOnly cookie).
 */
let platformRefreshInMemory: string | null = null;

/** Raised when an auth HTTP call fails; carries the status for caller mapping. */
export class AuthRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AuthRequestError";
    this.status = status;
  }
}

/**
 * Replace the in-memory access token (test/seam helper; also used internally on login/refresh).
 * Pass null to drop it. Does NOT touch the httpOnly refresh cookie (server-managed).
 */
export function setAccessToken(token: string | null): void {
  accessTokenInMemory = token;
}

/**
 * Clear all in-memory session state (access token + any platform refresh token). The company
 * refresh cookie is httpOnly and is cleared server-side by POST /auth/logout, not from here.
 */
export function clearSession(): void {
  accessTokenInMemory = null;
  platformRefreshInMemory = null;
}

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Log a company user in via POST /auth/login. The server sets the httpOnly refresh cookie;
 * the body carries the access token (stored in memory) + the user.
 *
 * Contract: returns the authenticated user on success. Throws AuthRequestError (status 401)
 * on invalid credentials — the message is generic by design.
 */
export async function login(email: string, password: string): Promise<AuthUser> {
  const response = await fetch(`${AUTH_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include", // accept the httpOnly Set-Cookie for the refresh token
    body: JSON.stringify({ email, password }),
  });
  const body = await parseJsonOrThrow<CompanyLoginResponse>(response);
  accessTokenInMemory = body.access_token;
  platformRefreshInMemory = null; // a company login supersedes any prior platform session
  return body.user;
}

/**
 * Log a platform admin in via POST /platform/login and store the token pair in memory.
 *
 * Contract: /platform/login returns tokens only (no user object); the caller resolves the
 * real admin identity separately via GET /platform/me (fetchCurrentPlatformAdmin). The
 * platform refresh token is held in MEMORY only, never persisted (AUD-14). Throws
 * AuthRequestError (401) on invalid credentials.
 */
export async function platformLogin(email: string, password: string): Promise<void> {
  const response = await fetch(`${AUTH_BASE}/platform/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const tokens = await parseJsonOrThrow<TokenPair>(response);
  accessTokenInMemory = tokens.access_token;
  platformRefreshInMemory = tokens.refresh_token; // memory only — never persisted
}

/**
 * In-flight refresh shared across concurrent callers (AUD-11). Backend rotation is
 * single-use, so two parallel refreshes would revoke each other's token and force a
 * spurious logout; sharing one promise lets concurrent 401s ride a single rotation.
 */
let refreshInFlight: Promise<string> | null = null;

/**
 * Rotate the active session's refresh token. Domain-aware: a PLATFORM session rotates via
 * POST /platform/refresh (in-memory token); a COMPANY session via POST /auth/refresh
 * (httpOnly cookie).
 *
 * Contract: returns the fresh access token on success. Concurrent callers share ONE in-flight
 * rotation (no double-rotation). Throws AuthRequestError when the server rejects the token
 * (revoked/expired/unknown -> 401) or there is no session; the in-memory session is cleared.
 */
export function refreshTokens(): Promise<string> {
  if (refreshInFlight === null) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function performRefresh(): Promise<string> {
  // Platform session: rotate via /platform/refresh using the in-memory platform token.
  if (platformRefreshInMemory !== null) {
    const response = await fetch(`${AUTH_BASE}/platform/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: platformRefreshInMemory }),
    });
    if (!response.ok) {
      clearSession();
      throw new AuthRequestError(response.status, "Platform refresh rejected");
    }
    const tokens = (await response.json()) as TokenPair;
    accessTokenInMemory = tokens.access_token;
    platformRefreshInMemory = tokens.refresh_token; // rotate the in-memory pair (still unpersisted)
    return tokens.access_token;
  }

  // Company session: serialize rotation ACROSS TABS (Web Locks) so two tabs never race the
  // single-use rotation. The httpOnly refresh cookie is sent + rotated by the browser.
  return withRefreshLock(rotateCompanySession);
}

/**
 * Run `task` while holding the cross-tab refresh lock (Web Locks API), so only one tab
 * rotates the company session at a time. Falls back to running directly where
 * `navigator.locks` is unavailable (older browsers, jsdom).
 */
function withRefreshLock<T>(task: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (locks !== undefined && typeof locks.request === "function") {
    return locks.request("oneai.auth.refresh", () => task()) as Promise<T>;
  }
  return task();
}

/**
 * Rotate the company session via POST /auth/refresh. The httpOnly refresh cookie is sent
 * automatically (credentials: include) and the rotated cookie is set on the response; the
 * body carries only the fresh access token. On rejection the in-memory session is cleared.
 */
async function rotateCompanySession(): Promise<string> {
  const response = await fetch(`${AUTH_BASE}/auth/refresh`, {
    method: "POST",
    credentials: "include", // send the httpOnly refresh cookie + accept the rotated one
  });
  if (!response.ok) {
    clearSession();
    throw new AuthRequestError(response.status, "Refresh rejected");
  }
  const body = (await response.json()) as AccessTokenResponse;
  accessTokenInMemory = body.access_token;
  return body.access_token;
}

/**
 * Revoke the active session server-side and clear local state. Domain-aware: a platform
 * session hits POST /platform/logout (in-memory token), a company session POST /auth/logout
 * (httpOnly cookie, which the server also clears).
 *
 * Contract: best-effort — the local session is cleared even if the network call fails, so the
 * user is always logged out locally.
 */
export async function logout(): Promise<void> {
  const platformRefresh = platformRefreshInMemory;
  try {
    if (platformRefresh !== null) {
      await fetch(`${AUTH_BASE}/platform/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: platformRefresh }),
      });
    } else {
      await fetch(`${AUTH_BASE}/auth/logout`, {
        method: "POST",
        credentials: "include", // send the httpOnly cookie so the server can revoke + clear it
      });
    }
  } catch {
    // Network failure on logout must not strand the user in a logged-in UI.
  }
  clearSession();
}

/**
 * Fetch the current company user via GET /auth/me, attaching the Bearer token and
 * refreshing once on 401.
 *
 * Contract: returns the AuthUser on success. Throws AuthRequestError (401) when no
 * valid session can be established — the caller treats that as "unauthenticated".
 */
export async function fetchCurrentUser(): Promise<AuthUser> {
  const response = await authorizedFetch(`${AUTH_BASE}/auth/me`);
  return parseJsonOrThrow<AuthUser>(response);
}

/**
 * Fetch the current platform admin via GET /platform/me, attaching the Bearer token and
 * refreshing once on 401. Returns the server-verified identity.
 *
 * Contract: returns the PlatformAdminView on success. Throws AuthRequestError (401) when
 * no valid platform session can be established.
 */
export async function fetchCurrentPlatformAdmin(): Promise<PlatformAdminView> {
  const response = await authorizedFetch(`${AUTH_BASE}/platform/me`);
  return parseJsonOrThrow<PlatformAdminView>(response);
}

/**
 * Authenticated fetch wrapper: attaches the in-memory access token and, on a 401,
 * rotates the refresh token once and replays the request.
 *
 * Contract: `init` is the standard RequestInit (method/body/headers). On a 401 it
 * attempts a single refresh, then replays the request once. The replay calls
 * `sendWithBearer` DIRECTLY (not `authorizedFetch`), so a second 401 surfaces to the
 * caller and can never re-enter the refresh path — no recursion, no loop.
 *
 * Edge cases: a request issued with no access token still goes out (server answers
 * 401), triggering the refresh path — this is how mount-time bootstrap rehydrates.
 */
export async function authorizedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const firstAttempt = await sendWithBearer(url, init);
  if (firstAttempt.status !== 401) {
    return firstAttempt;
  }

  // Single rotation attempt; refreshTokens clears the session on its own failure.
  const refreshedAccessToken = await refreshTokens();
  return sendWithBearer(url, init, refreshedAccessToken);
}

// Bounded request time for every authorized call: drawers block closing while a submit is
// in flight, so an unsettled fetch on a blackholed network would leave them undismissable
// until the browser's own (minutes-long) network timeout. 30s covers the 15s server-side
// IMAP verify with headroom; a caller-supplied init.signal always wins.
const REQUEST_TIMEOUT_MS = 30_000;

async function sendWithBearer(
  url: string,
  init: RequestInit,
  overrideAccessToken?: string,
): Promise<Response> {
  const bearer = overrideAccessToken ?? accessTokenInMemory;
  const headers = new Headers(init.headers);
  if (bearer !== null && bearer !== undefined) {
    headers.set("Authorization", `Bearer ${bearer}`);
  }
  const signal = init.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return fetch(url, { ...init, headers, signal });
}
