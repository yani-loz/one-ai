/**
 * Tests for the authenticated home — specifically the company_admin-only "Manage organisation"
 * entry point to /admin. The route guard (AdminRoute) is the real boundary and is tested
 * separately; this locks the link-visibility invariant the HomePage docstring claims ("other
 * roles never see it"). Session is injected via a controlled AuthContext; the /health probe
 * is stubbed at the fetch boundary.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "./identity/authContext";
import type { AuthUser, Role } from "./identity";
import { HomePage } from "./HomePage";

const MANAGE_LINK = /Manage organisation/;

function userOf(role: Role): AuthUser {
  const isPlatform = role === "platform_admin";
  return {
    id: "u-1",
    email: "person@acme.de",
    full_name: "Person",
    role,
    org_id: isPlatform ? null : "org-1",
    org_name: isPlatform ? null : "Acme GmbH",
  };
}

function renderHome(user: AuthUser): void {
  const value: AuthContextValue = {
    user,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout: vi.fn(),
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

beforeEach(() => {
  // HomePage probes /health on mount — stub fetch so it resolves without a real backend.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ service: "one-ai", version: "0.1.0", database: "ok" }),
      } as Response),
    ) as unknown as typeof fetch,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HomePage admin entry point", () => {
  it("test_company_admin_sees_manage_organisation_link", async () => {
    renderHome(userOf("company_admin"));
    await screen.findByText(/one-ai v0\.1\.0/i); // settle the async /health update (act)

    expect(screen.getByRole("link", { name: MANAGE_LINK })).toBeInTheDocument();
  });

  it("test_member_does_not_see_manage_organisation_link", async () => {
    renderHome(userOf("member"));
    await screen.findByText(/one-ai v0\.1\.0/i);

    expect(screen.queryByRole("link", { name: MANAGE_LINK })).not.toBeInTheDocument();
  });

  it("test_platform_admin_does_not_see_manage_organisation_link", async () => {
    renderHome(userOf("platform_admin"));
    await screen.findByText(/one-ai v0\.1\.0/i);

    expect(screen.queryByRole("link", { name: MANAGE_LINK })).not.toBeInTheDocument();
  });
});
