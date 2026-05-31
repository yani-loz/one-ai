/**
 * Role: Tests for ProtectedRoute and useAuth — covers the loading state (no redirect
 *       during bootstrap), the unauthenticated redirect, and the missing-provider guard.
 * Used by: vitest (pnpm test).
 * Depends on: a mocked global fetch (controls how/when bootstrap resolves), router.
 */
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from ".";
import { setTokens } from "./authClient";
import { ProtectedRoute } from "./ProtectedRoute";
import { useAuth } from "./useAuth";

function renderGuarded() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<p>login screen</p>} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <p>secret content</p>
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  setTokens(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  setTokens(null);
});

describe("ProtectedRoute", () => {
  it("test_loading_state_shows_skeleton_and_does_not_redirect", () => {
    // A stored refresh token + a never-resolving /auth/me keeps status === loading.
    localStorage.setItem("oneai.refresh_token", "r1");
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})) as unknown as typeof fetch);

    renderGuarded();

    expect(screen.getByRole("status", { name: "Restoring session" })).toBeInTheDocument();
    expect(screen.queryByText("login screen")).not.toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("test_unauthenticated_redirects_to_login", async () => {
    // No stored token -> bootstrap short-circuits to unauthenticated, no network call.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("unused"))) as unknown as typeof fetch,
    );

    renderGuarded();

    await waitFor(() => expect(screen.getByText("login screen")).toBeInTheDocument());
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });
});

describe("useAuth", () => {
  it("test_useAuth_outside_provider_throws", () => {
    expect(() => renderHook(() => useAuth())).toThrow(/within an <AuthProvider>/);
  });
});
