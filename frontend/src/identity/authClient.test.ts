/**
 * Role: Unit tests for the auth HTTP client — token storage, login/refresh/logout,
 *       and the 401 -> single-refresh -> replay behaviour of authorizedFetch.
 * Used by: vitest (pnpm test).
 * Depends on: a mocked global fetch (adapter boundary) and a real localStorage (jsdom).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AuthRequestError,
  fetchCurrentUser,
  getStoredRefreshToken,
  login,
  logout,
  platformLogin,
  refreshTokens,
  setTokens,
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

beforeEach(() => {
  localStorage.clear();
  setTokens(null);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  setTokens(null);
});

describe("login", () => {
  it("test_login_valid_credentials_stores_tokens_and_returns_user", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        access_token: "a1",
        refresh_token: "r1",
        token_type: "bearer",
        user: DEMO_USER,
      }),
    );

    const user = await login("admin@demo.oneai", "pw");

    expect(user.email).toBe("admin@demo.oneai");
    expect(getStoredRefreshToken()).toBe("r1");
  });

  it("test_login_invalid_credentials_throws_401_and_stores_nothing", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "invalid" }));

    await expect(login("admin@demo.oneai", "wrong")).rejects.toBeInstanceOf(AuthRequestError);
    expect(getStoredRefreshToken()).toBeNull();
  });
});

describe("platformLogin", () => {
  it("test_platform_login_valid_stores_token_pair", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "pa", refresh_token: "pr", token_type: "bearer" }),
    );

    await platformLogin("super@ethera.ai", "pw");

    const [calledUrl] = fetchMock.mock.calls[0];
    expect(String(calledUrl)).toContain("/platform/login");
    expect(getStoredRefreshToken()).toBe("pr");
  });
});

describe("refreshTokens", () => {
  it("test_refresh_no_stored_token_throws_without_network", async () => {
    await expect(refreshTokens()).rejects.toBeInstanceOf(AuthRequestError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("test_refresh_rejected_clears_session", async () => {
    setTokens({ access_token: "a1", refresh_token: "r1", token_type: "bearer" });
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));

    await expect(refreshTokens()).rejects.toBeInstanceOf(AuthRequestError);
    expect(getStoredRefreshToken()).toBeNull();
  });

  it("test_refresh_success_rotates_stored_token", async () => {
    setTokens({ access_token: "a1", refresh_token: "r1", token_type: "bearer" });
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "a2", refresh_token: "r2", token_type: "bearer" }),
    );

    const newAccess = await refreshTokens();

    expect(newAccess).toBe("a2");
    expect(getStoredRefreshToken()).toBe("r2");
  });

  it("test_concurrent_refresh_calls_share_one_rotation", async () => {
    // AUD-11: single-use backend rotation means parallel refreshes must not each POST
    // /auth/refresh (the second would present an already-revoked token and force a
    // spurious logout). Concurrent callers share ONE in-flight rotation.
    setTokens({ access_token: "a1", refresh_token: "r1", token_type: "bearer" });
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { access_token: "a2", refresh_token: "r2", token_type: "bearer" }),
    );

    const [first, second] = await Promise.all([refreshTokens(), refreshTokens()]);

    expect(first).toBe("a2");
    expect(second).toBe("a2");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("logout", () => {
  it("test_logout_revokes_and_clears_even_if_network_fails", async () => {
    setTokens({ access_token: "a1", refresh_token: "r1", token_type: "bearer" });
    fetchMock.mockRejectedValueOnce(new Error("network down"));

    await logout();

    expect(getStoredRefreshToken()).toBeNull();
  });
});

describe("fetchCurrentUser auto-refresh", () => {
  it("test_me_401_then_refresh_then_replay_returns_user", async () => {
    setTokens({ access_token: "stale", refresh_token: "r1", token_type: "bearer" });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // first /auth/me -> stale token
      .mockResolvedValueOnce(
        jsonResponse(200, { access_token: "fresh", refresh_token: "r2", token_type: "bearer" }),
      ) // /auth/refresh rotation
      .mockResolvedValueOnce(jsonResponse(200, DEMO_USER)); // replayed /auth/me

    const user = await fetchCurrentUser();

    expect(user.email).toBe("admin@demo.oneai");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(getStoredRefreshToken()).toBe("r2");
  });

  it("test_me_401_and_refresh_fails_propagates_error", async () => {
    setTokens({ access_token: "stale", refresh_token: "r1", token_type: "bearer" });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, {})) // /auth/me
      .mockResolvedValueOnce(jsonResponse(401, {})); // /auth/refresh rejects

    await expect(fetchCurrentUser()).rejects.toBeInstanceOf(AuthRequestError);
    expect(getStoredRefreshToken()).toBeNull();
  });
});
