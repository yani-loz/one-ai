/**
 * Integration tests for the org detail screen — renders with a controlled platform-admin
 * AuthContext and a URL+method-aware mocked /platform/orgs/:id boundary, covering render,
 * suspend/reactivate, legal-hold toggle, back navigation, the error state, and 401→logout.
 * Canvas is stubbed to null in setup (BrandMark inert).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { OrganizationDetailPage } from "./OrganizationDetailPage";

const PLATFORM_ADMIN: AuthUser = {
  id: "platform-admin",
  email: "super@ethera.ai",
  full_name: "super",
  role: "platform_admin",
  org_id: null,
  org_name: null,
};

const DETAIL = {
  id: "org-1",
  name: "Acme GmbH",
  slug: "acme",
  status: "active",
  user_count: 3,
  legal_hold: false,
  created_at: "2026-06-01T10:00:00Z",
};

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

/** GET returns the detail; PATCH echoes the request body into the returned detail. */
function mockApi(getStatus = 200): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/platform/orgs/org-1") && method === "GET") return json(getStatus, DETAIL);
      if (url.includes("/platform/orgs/org-1/status") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as { status: string };
        return json(200, { ...DETAIL, status: body.status });
      }
      if (url.includes("/platform/orgs/org-1/legal-hold") && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as { legal_hold: boolean };
        return json(200, { ...DETAIL, legal_hold: body.legal_hold });
      }
      return json(404, {});
    }) as unknown as typeof fetch,
  );
}

function renderDetail(logout: () => Promise<void> = vi.fn(() => Promise.resolve())) {
  const value: AuthContextValue = {
    user: PLATFORM_ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  return render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={["/platform/orgs/org-1"]}>
        <Routes>
          <Route path="/platform/orgs/:orgId" element={<OrganizationDetailPage />} />
          <Route path="/platform" element={<div>FLEET</div>} />
        </Routes>
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

describe("OrganizationDetailPage", () => {
  it("test_renders_detail_with_governance_slots_and_sealed_banner", async () => {
    mockApi();

    renderDetail();

    expect(await screen.findByRole("heading", { name: "Acme GmbH" })).toBeInTheDocument();
    expect(screen.getByText("Data residency / region")).toBeInTheDocument();
    expect(screen.getByText(/Company content is sealed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Suspend access" })).toBeInTheDocument();
  });

  it("test_suspend_action_flips_to_reactivate", async () => {
    const user = userEvent.setup();
    mockApi();

    renderDetail();
    await user.click(await screen.findByRole("button", { name: "Suspend access" }));

    expect(await screen.findByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    expect(screen.getByText(/Company sign-in is blocked/)).toBeInTheDocument();
  });

  it("test_legal_hold_toggles_on", async () => {
    const user = userEvent.setup();
    mockApi();

    renderDetail();
    await user.click(await screen.findByRole("button", { name: "Place hold" }));

    expect(await screen.findByRole("button", { name: "Clear hold" })).toBeInTheDocument();
  });

  it("test_back_navigates_to_the_fleet", async () => {
    const user = userEvent.setup();
    mockApi();

    renderDetail();
    await user.click(await screen.findByRole("button", { name: /Back to companies/ }));

    expect(screen.getByText("FLEET")).toBeInTheDocument();
  });

  it("test_load_error_shows_retry", async () => {
    mockApi(500);

    renderDetail();

    expect(await screen.findByText(/Couldn't load this company/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("test_401_clears_the_session", async () => {
    const logout = vi.fn(() => Promise.resolve());
    mockApi(401);

    renderDetail(logout);

    await waitFor(() => expect(logout).toHaveBeenCalled());
  });
});
