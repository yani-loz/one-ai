/**
 * Role: Route-shell tests — verifies that an unauthenticated visit to "/" redirects
 *       to /login, and that the login screen renders inside the App router shell.
 * Used by: vitest (pnpm test).
 * Depends on: a live AuthProvider (no router context comes from App itself; tests
 *   supply MemoryRouter) and a mocked fetch boundary so bootstrap resolves offline.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AuthProvider } from "./identity";
import type { AuthUser } from "./identity";
import { AuthContext, type AuthContextValue } from "./identity/authContext";

function mockFetch(impl: () => Promise<unknown>): void {
  vi.stubGlobal("fetch", vi.fn(impl) as unknown as typeof fetch);
}

function renderAppAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("App route shell", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders the login screen on the public /login route", async () => {
    mockFetch(() => Promise.reject(new Error("offline")));

    renderAppAt("/login");

    expect(await screen.findByRole("heading", { name: "One AI" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("redirects an unauthenticated visit to / onto the login screen", async () => {
    mockFetch(() => Promise.reject(new Error("offline")));

    renderAppAt("/");

    await waitFor(() => expect(screen.getByLabelText("Password")).toBeInTheDocument());
    expect(screen.getByText("Sign in to your workspace")).toBeInTheDocument();
  });

  it("routes an authenticated platform admin from / onward to the console", async () => {
    // Bypass AuthProvider with a controlled context so the platform admin is already
    // authenticated; RoleHome at "/" must forward to the platform console.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          String(input).includes("/platform/orgs")
            ? ({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response)
            : ({ ok: false, status: 401, json: () => Promise.resolve({}) } as Response),
        ),
      ) as unknown as typeof fetch,
    );
    const platformAdmin: AuthUser = {
      id: "platform-admin",
      email: "super@ethera.ai",
      full_name: "super",
      role: "platform_admin",
      org_id: null,
      org_name: null,
    };
    const value: AuthContextValue = {
      user: platformAdmin,
      status: "authenticated",
      login: vi.fn(),
      platformLogin: vi.fn(),
      logout: vi.fn(),
    };

    render(
      <AuthContext.Provider value={value}>
        <MemoryRouter initialEntries={["/"]}>
          <App />
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(await screen.findByRole("heading", { name: "Companies" })).toBeInTheDocument();
  });
});
