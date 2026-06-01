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
 *     NEVER persisted (platformLogin calls setTokens(tokens, false)) — so an injected
 *     script can never read a 7-day Ethera-staff credential from storage.
 *   - `authorizedFetch` retries a 401 at most ONCE, and never tries to refresh the
 *     /auth/refresh or /auth/login calls themselves (no infinite loop).
 *   - A failed refresh clears the whole session so callers fall back to /login.
 */
import type { AuthUser, CompanyLoginResponse, TokenPair } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const REFRESH_STORAGE_KEY = "oneai.refresh_token";

/** Access token lives only in module memory — gone on hard refresh by design. */
let accessTokenInMemory: string | null = null;

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
 * pair, the access token always goes to memory; `persistRefresh` decides whether the
 * refresh token is written to localStorage. Company logins persist it (so the session
 * rehydrates on reload). The platform login passes `false`: the platform refresh token
 * is NOT persisted — it is discarded client-side, so a platform session lasts one
 * access-token lifetime until /platform/refresh lands (PR-2, AUD-14). This keeps the
 * high-privilege platform credential out of XSS-readable storage.
 */
export function setTokens(tokens: TokenPair | null, persistRefresh = true): void {
  if (tokens === null) {
    accessTokenInMemory = null;
    localStorage.removeItem(REFRESH_STORAGE_KEY);
    return;
  }
  accessTokenInMemory = tokens.access_token;
  if (persistRefresh) {
    localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token);
  } else {
    // Platform session: do not persist the platform refresh token, and clear any stale
    // company token so the two domains never share localStorage state.
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
 * Log a platform admin in via POST /platform/login and store the token pair.
 *
 * Contract: /platform/login returns tokens only (no user object, no /platform/me),
 * so the caller synthesises the in-memory admin identity from the entered email. The
 * platform refresh token is NOT persisted (setTokens(tokens, false)) — kept out of
 * localStorage so it can never be exfiltrated by injected script. Throws
 * AuthRequestError (401) on invalid credentials.
 */
export async function platformLogin(email: string, password: string): Promise<void> {
  const response = await fetch(`${API_URL}/platform/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const tokens = await parseJsonOrThrow<TokenPair>(response);
  setTokens(tokens, false); // in-memory access only — never persist the platform refresh token
}

/**
 * In-flight refresh shared across concurrent callers (AUD-11). Backend rotation is
 * single-use, so two parallel refreshes would revoke each other's token and force a
 * spurious logout; sharing one promise lets concurrent 401s ride a single rotation.
 */
let refreshInFlight: Promise<string> | null = null;

/**
 * Rotate the stored refresh token via POST /auth/refresh.
 *
 * Contract: returns the fresh access token on success and persists the rotated pair.
 * Concurrent callers share ONE in-flight rotation (no double-rotation). Throws
 * AuthRequestError when there is no stored token or the server rejects it
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
    setTokens(null);
    throw new AuthRequestError(response.status, "Refresh rejected");
  }
  const tokens = (await response.json()) as TokenPair;
  setTokens(tokens);
  return tokens.access_token;
}

/**
 * Revoke the stored refresh token server-side (POST /auth/logout) and clear state.
 *
 * Contract: best-effort — local session is cleared even if the network call fails,
 * so the user is always logged out locally.
 */
export async function logout(): Promise<void> {
  const stored = getStoredRefreshToken();
  if (stored !== null) {
    try {
      await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: stored }),
      });
    } catch {
      // Network failure on logout must not strand the user in a logged-in UI.
    }
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
  return fetch(url, { ...init, headers });
}
