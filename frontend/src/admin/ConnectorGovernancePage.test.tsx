/**
 * Tests for the Tier-2 connector governance SCREEN wrapper — it renders its header, the §7 "you see
 * health, never the mailbox" note, and the embedded governance panel (driven by the mocked
 * governance + users endpoints). Global fetch is mocked at the boundary; session = a company-admin.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { ConnectorGovernancePage } from "./ConnectorGovernancePage";

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

const ADMIN: AuthUser = {
  id: "admin-1",
  email: "admin@acme.de",
  full_name: "Anna Admin",
  role: "company_admin",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

function installFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/users")) return Promise.resolve(json(200, []));
      if (url.includes("/governance/")) {
        return Promise.resolve(
          json(200, {
            connector_type: "imap",
            entitled: true,
            org_wide_enabled: false,
            overrides: [],
            connections: [],
          }),
        );
      }
      return Promise.resolve(json(404, {}));
    }) as unknown as typeof fetch,
  );
}

function renderPage() {
  const value: AuthContextValue = {
    user: ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout: vi.fn(),
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter>
        <ConnectorGovernancePage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ConnectorGovernancePage", () => {
  it("test_renders_header_and_section7_note_and_panel", async () => {
    installFetch();

    renderPage();

    expect(screen.getByRole("heading", { name: "Connector access" })).toBeInTheDocument();
    expect(screen.getByText(/never anyone's mailbox or its contents/)).toBeInTheDocument();
    // The embedded panel loads its governance view → the org-wide toggle appears.
    expect(await screen.findByRole("button", { name: "Enable org-wide" })).toBeInTheDocument();
  });
});
