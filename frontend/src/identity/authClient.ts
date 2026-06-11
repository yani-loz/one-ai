/**
 * Role: Thin HTTP client for the Identity backend — owns token storage (access in
 *       memory; the COMPANY refresh token in localStorage), login/refresh/logout/me, and
 *       a fetch wrapper that attaches the Bearer token and transparently refreshes once
 *       on 401.
 * Used by: AuthProvider.tsx (login/refresh/logout/me). `authorizedFetch` +
 *   `AuthRequestError` are also re-exported via ./index for sibling modules (platform/)
 *   to make authenticated calls without re-implementing token attachment.
 * Depends on: ./types, import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - The access token NEVER touches localStorage (XSS-exposure reduction). Only the
 *     COMPANY refresh token is persisted; the high-privilege PLATFORM refresh token is
 *     kept IN MEMORY ONLY (never localStorage) — used for /platform/refresh and
 *     /platform/logout, but gone on reload, so an injected script can never read a
 *     7-day Ethera-staff credential from storage.
 *   - `performRefresh` / `logout` are domain-aware: a platform session rotates/revokes
 *     via /platform/*; a company session via /auth/* (its refresh token in localStorage).
 *   - Company refresh is serialized ACROSS TABS (Web Locks API) plus a compare-and-clear
 *     guard, so two tabs racing to rotate the SHARED localStorage token can't log each
 *     other out (TC-FE-009). Platform refresh is in-memory (single-tab) and needs no lock.
 *   - `authorizedFetch` retries a 401 at most ONCE, and never tries to refresh the
 *     /auth/refresh or /auth/login calls themselves (no infinite loop).
 *   - Every authorized request is TIME-BOUNDED (30s AbortSignal.timeout unless the caller
 *     passes its own signal): drawers block closing mid-submit, so an unsettled fetch on a
 *     blackholed network must never wedge the UI until the browser's own timeout.
 *   - A failed refresh clears the session — compare-and-clear: UNLESS another tab already
 *     rotated the token — so callers fall back to /login.
 */
import type { AuthUser, CompanyLoginResponse, PlatformAdminView, TokenPair } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const REFRESH_STORAGE_KEY = "oneai.refresh_token";

/** Access token lives only in module memory — gone on hard refresh by design. */
let accessTokenInMemory: string | null = null;

/**
 * Platform refresh token — kept in MEMORY ONLY (never localStorage) so the
 * high-privilege platform session can refresh/log out within a tab without exposing the
 * credential to XSS. Null for a company session (whose refresh lives in localStorage).
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

/** Persisted opaque refresh token, or null when no session is stored. */
export function getStoredRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_STORAGE_KEY);
}

/**
 * Replace the in-memory access token and (optionally) the persisted refresh token.
 *
 * Contract: pass `null` to clear the session (logout / refresh failure). With a token
 * pair, the access token always goes to memory; `persistRefresh` decides where the
 * refresh token lives. Company logins persist it to localStorage (so the session
 * rehydrates on reload). The platform login passes `false`: the platform refresh token
 * is held in MEMORY only (never localStorage) so it survives in-tab refresh/logout but
 * is gone on reload — keeping the high-privilege credential out of XSS-readable storage.
 */
export function setTokens(tokens: TokenPair | null, persistRefresh = true): void {
  if (tokens === null) {
    accessTokenInMemory = null;
    platformRefreshInMemory = null;
    localStorage.removeItem(REFRESH_STORAGE_KEY);
    return;
  }
  accessTokenInMemory = tokens.access_token;
  if (persistRefresh) {
    platformRefreshInMemory = null;
    localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token);
  } else {
    // Platform session: hold the refresh token in MEMORY only (for /platform/refresh +
    // /platform/logout) and clear any stale company token from localStorage.
    platformRefreshInMemory = tokens.refresh_token;
    localStorage.removeItem(REFRESH_STORAGE_KEY);
  }
}

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Log a company user in via POST /auth/login and store the issued token pair.
 *
 * Contract: returns the authenticated user on success. Throws AuthRequestError
 * (status 401) on invalid credentials — the message is generic by design.
 */
export async function login(email: string, password: string): Promise<AuthUser> {
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await parseJsonOrThrow<CompanyLoginResponse>(response);
  setTokens(body);
  return body.user;
}

/**
 * Log a platform admin in via POST /platform/login and store the token pair in memory.
 *
 * Contract: /platform/login returns tokens only (no user object); the caller resolves the
 * real admin identity separately via GET /platform/me (fetchCurrentPlatformAdmin). The
 * platform refresh token is held in MEMORY only (setTokens(tokens, false)), never
 * localStorage. Throws AuthRequestError (401) on invalid credentials.
 */
export async function platformLogin(email: string, password: string): Promise<void> {
  const response = await fetch(`${API_URL}/platform/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const tokens = await parseJsonOrThrow<TokenPair>(response);
  setTokens(tokens, false); // store in memory only — the platform refresh token is never persisted
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
 * (localStorage token).
 *
 * Contract: returns the fresh access token on success and stores the rotated pair in the
 * same place. Concurrent callers share ONE in-flight rotation (no double-rotation).
 * Throws AuthRequestError when there is no token or the server rejects it
 * (revoked/expired/unknown -> 401); the session is cleared on failure.
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
    const response = await fetch(`${API_URL}/platform/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: platformRefreshInMemory }),
    });
    if (!response.ok) {
      setTokens(null);
      throw new AuthRequestError(response.status, "Platform refresh rejected");
    }
    const tokens = (await response.json()) as TokenPair;
    setTokens(tokens, false); // rotate the in-memory platform pair (still not persisted)
    return tokens.access_token;
  }

  // Company session: serialize rotation ACROSS TABS (Web Locks) so two tabs never rotate
  // the same localStorage token at once and the loser never wipes the winner's session
  // (TC-FE-009). Falls back to direct execution (guarded by compare-and-clear) where the
  // Web Locks API is unavailable.
  return withRefreshLock(rotateCompanySession);
}

/**
 * Run `task` while holding the cross-tab refresh lock (Web Locks API), so only one tab
 * rotates the shared company refresh token at a time. Falls back to running directly where
 * `navigator.locks` is unavailable (older browsers, jsdom) — the compare-and-clear guard in
 * `rotateCompanySession` still applies on that path.
 */
function withRefreshLock<T>(task: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (locks !== undefined && typeof locks.request === "function") {
    return locks.request("oneai.auth.refresh", () => task()) as Promise<T>;
  }
  return task();
}

/**
 * Rotate the company session via POST /auth/refresh. The stored token is re-read here so
 * (under the cross-tab lock) we always rotate the CURRENT token, not a stale captured one.
 *
 * Multi-tab safety (TC-FE-009): on a 401, only clear the session if OUR token is still the
 * stored one. If another tab rotated it while our request was in flight (the no-Web-Locks
 * fallback race), the winner's fresh token is live — surface the failure WITHOUT wiping it.
 */
async function rotateCompanySession(): Promise<string> {
  const stored = getStoredRefreshToken();
  if (stored === null) {
    throw new AuthRequestError(401, "No refresh token");
  }
  const response = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: stored }),
  });
  if (!response.ok) {
    if (getStoredRefreshToken() === stored) {
      setTokens(null);
      throw new AuthRequestError(response.status, "Refresh rejected");
    }
    throw new AuthRequestError(response.status, "Refresh superseded by another tab");
  }
  const tokens = (await response.json()) as TokenPair;
  setTokens(tokens);
  return tokens.access_token;
}

/**
 * Revoke the active session's refresh token server-side and clear local state.
 * Domain-aware: a platform session hits POST /platform/logout (in-memory token), a
 * company session POST /auth/logout (localStorage token).
 *
 * Contract: best-effort — the local session is cleared even if the network call fails,
 * so the user is always logged out locally.
 */
export async function logout(): Promise<void> {
  const platformRefresh = platformRefreshInMemory;
  const companyRefresh = getStoredRefreshToken();
  try {
    if (platformRefresh !== null) {
      await fetch(`${API_URL}/platform/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: platformRefresh }),
      });
    } else if (companyRefresh !== null) {
      await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: companyRefresh }),
      });
    }
  } catch {
    // Network failure on logout must not strand the user in a logged-in UI.
  }
  setTokens(null);
}

/**
 * Fetch the current company user via GET /auth/me, attaching the Bearer token and
 * refreshing once on 401.
 *
 * Contract: returns the AuthUser on success. Throws AuthRequestError (401) when no
 * valid session can be established — the caller treats that as "unauthenticated".
 */
export async function fetchCurrentUser(): Promise<AuthUser> {
  const response = await authorizedFetch(`${API_URL}/auth/me`);
  return parseJsonOrThrow<AuthUser>(response);
}

/**
 * Fetch the current platform admin via GET /platform/me, attaching the Bearer token and
 * refreshing once on 401. Returns the server-verified identity (replacing the prior
 * email-synthesised stand-in).
 *
 * Contract: returns the PlatformAdminView on success. Throws AuthRequestError (401) when
 * no valid platform session can be established.
 */
export async function fetchCurrentPlatformAdmin(): Promise<PlatformAdminView> {
  const response = await authorizedFetch(`${API_URL}/platform/me`);
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
