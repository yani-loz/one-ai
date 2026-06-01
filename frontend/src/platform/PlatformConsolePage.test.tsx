/**
 * Integration tests for the platform console — renders the page with a controlled
 * platform-admin AuthContext (no login flow) and a mocked /platform/orgs boundary,
 * covering the company list, search filtering, empty/error states, and the 401→logout
 * path. Canvas is stubbed to null in test setup, so BrandMark renders inertly.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { PlatformConsolePage } from "./PlatformConsolePage";

const PLATFORM_ADMIN: AuthUser = {
  id: "platform-admin",
  email: "super@ethera.ai",
  full_name: "super",
  role: "platform_admin",
  org_id: null,
  org_name: null,
};

const TWO_COMPANIES = [
  {
    id: "org-1",
    name: "Acme GmbH",
    slug: "acme",
    status: "active",
    user_count: 3,
    created_at: "2026-06-01T10:00:00Z",
  },
  {
    id: "org-2",
    name: "Nord SE",
    slug: "nord",
    status: "suspended",
    user_count: 0,
    created_at: "2026-05-20T10:00:00Z",
  },
];

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

function authValue(logout: () => Promise<void>): AuthContextValue {
  return {
    user: PLATFORM_ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
}

function renderConsole(logout: () => Promise<void> = vi.fn()) {
  return render(
    <AuthContext.Provider value={authValue(logout)}>
      <MemoryRouter>
        <PlatformConsolePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("PlatformConsolePage", () => {
  it("test_renders_company_list_and_sealed_banner", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, TWO_COMPANIES))));

    renderConsole();

    expect(await screen.findByText("Acme GmbH")).toBeInTheDocument();
    expect(screen.getByText("Nord SE")).toBeInTheDocument();
    expect(screen.getByText(/Company content is sealed/)).toBeInTheDocument();
  });

  it("test_search_filters_the_list", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, TWO_COMPANIES))));

    renderConsole();
    await screen.findByText("Acme GmbH");

    await user.type(screen.getByLabelText("Search companies"), "nord");

    expect(screen.queryByText("Acme GmbH")).not.toBeInTheDocument();
    expect(screen.getByText("Nord SE")).toBeInTheDocument();
  });

  it("test_search_no_match_shows_distinct_message", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, TWO_COMPANIES))));

    renderConsole();
    await screen.findByText("Acme GmbH");

    await user.type(screen.getByLabelText("Search companies"), "zzz");

    expect(screen.getByText("No companies match your search.")).toBeInTheDocument();
    expect(screen.queryByText(/No companies yet/)).not.toBeInTheDocument();
  });

  it("test_empty_fleet_shows_onboarding_hint", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, []))));

    renderConsole();

    expect(await screen.findByText(/No companies yet/)).toBeInTheDocument();
  });

  it("test_error_state_shows_retry", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(500, {}))));

    renderConsole();

    expect(await screen.findByText(/Couldn't load companies/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("test_401_clears_the_session", async () => {
    // No stored refresh token → the 401 refresh attempt fails and surfaces as a 401,
    // so the console logs out and lets the route guard redirect to /login.
    const logout = vi.fn(() => Promise.resolve());
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(401, {}))));

    renderConsole(logout);

    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
