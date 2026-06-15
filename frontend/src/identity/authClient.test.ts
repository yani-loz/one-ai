/**
 * Role: Unit tests for the auth HTTP client — the company plane (httpOnly-cookie refresh,
 *       access token in memory), the platform plane (in-memory body-based refresh), and the
 *       401 -> single-refresh -> replay behaviour of authorizedFetch.
 * Used by: vitest (pnpm test).
 * Depends on: a mocked global fetch (adapter boundary). No localStorage / no readable refresh
 *   token: the company refresh token is an httpOnly cookie the browser manages — these tests
 *   assert the request shape (credentials, URL, attached Bearer), not a stored token value.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authorizedFetch,
  AuthRequestError,
  clearSession,
  fetchCurrentUser,
  login,
  logout,
  platformLogin,
  refreshTokens,
  setAccessToken,
} from "./authClient";
import type { AuthUser } from "./types";

const DEMO_USER: AuthUser = {
  id: "u1",
  email: "admin@demo.oneai",
  full_name: "Demo Admin",
  role: "company_admin",
  org_id: "org-1",
  org_name: "One AI Demo GmbH",
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

/** The RequestInit of the Nth fetch call (default: the last). */
function initOf(callIndex = fetchMock.mock.calls.length - 1): RequestInit {
  return (fetchMock.mock.calls[callIndex]?.[1] ?? {}) as RequestInit;
}

/** The Authorization header a fetch call carried, or null. */
function authHeaderOf(init: RequestInit): string | null {
  return new Headers(init.headers).get("Authorization");
}

beforeEach(() => {
  clearSession();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});

describe("login (company)", () => {
  it("test_login_valid_credentials_stores_access_token_and_returns_user", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "a1", token_type: "bearer", user: DEMO_USER }),
    );

    const user = await login("admin@demo.oneai", "pw");

    expect(user.email).toBe("admin@demo.oneai");
    // The login request sends credentials so the httpOnly refresh Set-Cookie is accepted.
    expect(initOf(0).credentials).toBe("include");
    // The access token is now attached as Bearer on authorized calls (proves it was stored).
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await authorizedFetch("/some/protected");
    expect(authHeaderOf(initOf())).toBe("Bearer a1");
  });

  it("test_login_invalid_credentials_throws_401_and_stores_nothing", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "invalid" }));

    await expect(login("admin@demo.oneai", "wrong")).rejects.toBeInstanceOf(AuthRequestError);
    // No token stored: a later authorized call carries no Authorization header.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await authorizedFetch("/some/protected").catch(() => undefined);
    expect(authHeaderOf(initOf())).toBeNull();
  });
});

describe("platformLogin", () => {
  it("test_platform_login_stores_token_in_memory_and_refreshes_via_platform_endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
    );
    await platformLogin("super@ethera.ai", "pw");

    const [calledUrl] = fetchMock.mock.calls[0];
    expect(String(calledUrl)).toContain("/platform/login");

    // The in-memory platform token drives /platform/refresh (proving it was stored in memory).
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "pa2", refresh_token: "pr2", token_type: "bearer" }),
    );
    const fresh = await refreshTokens();
    expect(fresh).toBe("pa2");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/platform/refresh");
  });
});

describe("platform session (domain-aware refresh + logout)", () => {
  it("test_platform_session_refreshes_via_platform_endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
    );
    await platformLogin("super@ethera.ai", "pw");

    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // protected call -> stale access token
      .mockResolvedValueOnce(
        jsonResponse(200, { access_token: "pa2", refresh_token: "pr2", token_type: "bearer" }),
      ) // /platform/refresh rotation
      .mockResolvedValueOnce(jsonResponse(200, { ok: true })); // replayed protected call

    const response = await authorizedFetch("http://test/platform/orgs");

    expect(response.status).toBe(200);
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("/platform/refresh"))).toBe(true);
    // It must NOT fall back to the company refresh endpoint for a platform session.
    expect(urls.some((url) => url.includes("/auth/refresh"))).toBe(false);
  });

  it("test_platform_logout_revokes_via_platform_endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
    );
    await platformLogin("super@ethera.ai", "pw");
    fetchMock.mockResolvedValueOnce(jsonResponse(204, {}));

    await logout();

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("/platform/logout"))).toBe(true);
  });
});

describe("refreshTokens (company, httpOnly cookie)", () => {
  it("test_company_refresh_success_updates_access_token_via_cookie", async () => {
    setAccessToken("a1"); // an established company session (token in memory, refresh in cookie)
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { access_token: "a2", token_type: "bearer" }));

    const newAccess = await refreshTokens();

    expect(newAccess).toBe("a2");
    // The refresh hit /auth/refresh WITH credentials (the cookie) and NO body token.
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/auth/refresh");
    expect(initOf(0).credentials).toBe("include");
    expect(initOf(0).body).toBeUndefined();
  });

  it("test_company_refresh_rejected_clears_session", async () => {
    setAccessToken("a1");
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));

    await expect(refreshTokens()).rejects.toBeInstanceOf(AuthRequestError);
    // Session cleared: a later authorized call carries no Authorization header.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await authorizedFetch("/x").catch(() => undefined);
    expect(authHeaderOf(initOf())).toBeNull();
  });

  it("test_concurrent_refresh_calls_share_one_rotation", async () => {
    // AUD-11: single-use backend rotation means parallel refreshes must not each POST
    // /auth/refresh (the second would present an already-revoked cookie and force a spurious
    // logout). Concurrent callers share ONE in-flight rotation.
    setAccessToken("a1");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { access_token: "a2", token_type: "bearer" }));

    const [first, second] = await Promise.all([refreshTokens(), refreshTokens()]);

    expect(first).toBe("a2");
    expect(second).toBe("a2");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("logout (company)", () => {
  it("test_company_logout_revokes_via_auth_endpoint_with_credentials", async () => {
    setAccessToken("a1");
    fetchMock.mockResolvedValueOnce(jsonResponse(204, {}));

    await logout();

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/auth/logout");
    expect(initOf(0).credentials).toBe("include"); // send the cookie so the server clears it
  });

  it("test_logout_clears_session_even_if_network_fails", async () => {
    setAccessToken("a1");
    fetchMock.mockRejectedValueOnce(new Error("network down"));

    await logout();

    // Session cleared locally despite the failed revoke: no Bearer on the next call.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await authorizedFetch("/x").catch(() => undefined);
    expect(authHeaderOf(initOf())).toBeNull();
  });
});

describe("fetchCurrentUser auto-refresh", () => {
  it("test_me_401_then_refresh_then_replay_returns_user", async () => {
    setAccessToken("stale");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // first /auth/me -> stale token
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "fresh", token_type: "bearer" })) // /auth/refresh
      .mockResolvedValueOnce(jsonResponse(200, DEMO_USER)); // replayed /auth/me

    const user = await fetchCurrentUser();

    expect(user.email).toBe("admin@demo.oneai");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // The replayed /auth/me carried the FRESH token from the rotation.
    expect(authHeaderOf(initOf(2))).toBe("Bearer fresh");
  });

  it("test_me_401_and_refresh_fails_propagates_error", async () => {
    setAccessToken("stale");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // /auth/me
      .mockResolvedValueOnce(jsonResponse(401, {})); // /auth/refresh rejects

    await expect(fetchCurrentUser()).rejects.toBeInstanceOf(AuthRequestError);
  });
});
