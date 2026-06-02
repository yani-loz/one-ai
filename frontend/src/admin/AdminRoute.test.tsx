/**
 * Tests for the company-admin route guard — only an authenticated company_admin reaches the
 * console; members and platform admins are sent to "/", anonymous visitors to /login, and a
 * still-loading session shows a skeleton (never redirects). The role gate is the frontend
 * analogue of the backend cross-tenant negative test (testing.md): assert BOTH the redirect
 * AND that the protected content is absent. Uses a controlled AuthContext value so no
 * network/login flow runs.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { AdminRoute } from "./AdminRoute";

const COMPANY_ADMIN: AuthUser = {
  id: "u-1",
  email: "admin@acme.de",
  full_name: "Anna",
  role: "company_admin",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

const MEMBER: AuthUser = {
  id: "u-2",
  email: "member@acme.de",
  full_name: "Bob",
  role: "member",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

const PLATFORM_ADMIN: AuthUser = {
  id: "platform-admin",
  email: "super@ethera.ai",
  full_name: "super",
  role: "platform_admin",
  org_id: null,
  org_name: null,
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
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <div>CONSOLE</div>
              </AdminRoute>
            }
          />
          <Route path="/" element={<div>HOME</div>} />
          <Route path="/login" element={<div>LOGIN</div>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("AdminRoute", () => {
  it("test_company_admin_reaches_console", () => {
    renderGuard(authValue({ status: "authenticated", user: COMPANY_ADMIN }));

    expect(screen.getByText("CONSOLE")).toBeInTheDocument();
  });

  it("test_member_is_redirected_to_home_and_console_absent", () => {
    renderGuard(authValue({ status: "authenticated", user: MEMBER }));

    expect(screen.getByText("HOME")).toBeInTheDocument();
    expect(screen.queryByText("CONSOLE")).not.toBeInTheDocument();
  });

  it("test_platform_admin_is_redirected_to_home_and_console_absent", () => {
    renderGuard(authValue({ status: "authenticated", user: PLATFORM_ADMIN }));

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
