/**
 * Tests for the platform route guard — only an authenticated platform admin reaches the
 * console; company users are sent to "/", anonymous visitors to /login, and a still-
 * loading session shows a skeleton (never redirects). Uses a controlled AuthContext
 * value (imported from the module-internal authContext) so no network/login flow runs.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { PlatformRoute } from "./PlatformRoute";

const PLATFORM_ADMIN: AuthUser = {
  id: "platform-admin",
  email: "super@ethera.ai",
  full_name: "super",
  role: "platform_admin",
  org_id: null,
  org_name: null,
};

const COMPANY_ADMIN: AuthUser = {
  id: "u-1",
  email: "admin@acme.de",
  full_name: "Anna",
  role: "company_admin",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

function authValue(overrides: Partial<AuthContextValue>): AuthContextValue {
  return {
    user: null,
    status: "unauthenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout: vi.fn(),
    ...overrides,
  };
}

function renderGuard(value: AuthContextValue) {
  return render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={["/platform"]}>
        <Routes>
          <Route
            path="/platform"
            element={
              <PlatformRoute>
                <div>CONSOLE</div>
              </PlatformRoute>
            }
          />
          <Route path="/" element={<div>HOME</div>} />
          <Route path="/login" element={<div>LOGIN</div>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("PlatformRoute", () => {
  it("test_platform_admin_reaches_console", () => {
    renderGuard(authValue({ status: "authenticated", user: PLATFORM_ADMIN }));

    expect(screen.getByText("CONSOLE")).toBeInTheDocument();
  });

  it("test_company_user_is_redirected_to_home", () => {
    renderGuard(authValue({ status: "authenticated", user: COMPANY_ADMIN }));

    expect(screen.getByText("HOME")).toBeInTheDocument();
    expect(screen.queryByText("CONSOLE")).not.toBeInTheDocument();
  });

  it("test_anonymous_visitor_is_redirected_to_login", () => {
    renderGuard(authValue({ status: "unauthenticated", user: null }));

    expect(screen.getByText("LOGIN")).toBeInTheDocument();
    expect(screen.queryByText("CONSOLE")).not.toBeInTheDocument();
  });

  it("test_loading_session_shows_skeleton_without_redirecting", () => {
    renderGuard(authValue({ status: "loading", user: null }));

    expect(screen.getByRole("status", { name: "Restoring session" })).toBeInTheDocument();
    expect(screen.queryByText("CONSOLE")).not.toBeInTheDocument();
    expect(screen.queryByText("LOGIN")).not.toBeInTheDocument();
  });
});
