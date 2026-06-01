/**
 * Tests for the company-side break-glass approval inbox — renders a pending request (who +
 * why + approve/deny), and approving it re-fetches and flips the row to an active grant with
 * a revoke. Mocked fetch boundary; the approve POST mutates the mock like the backend.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../identity";
import { AuthContext, type AuthContextValue } from "../identity/authContext";
import { SupportInbox } from "./SupportInbox";
import type { SupportGrant } from "./types";

const COMPANY_ADMIN: AuthUser = {
  id: "u-1",
  email: "admin@acme.example",
  full_name: "Admin",
  role: "company_admin",
  org_id: "org-1",
  org_name: "Acme",
};

function grant(overrides: Partial<SupportGrant>): SupportGrant {
  return {
    id: "g-1",
    org_id: "org-1",
    requested_by_admin_id: "a-1",
    requested_by_email: "staff@ethera.ai",
    reason: "Investigate failed sync",
    status: "requested",
    is_active: false,
    decided_at: null,
    decided_by_email: null,
    expires_at: null,
    created_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

let inbox: SupportGrant[] = [];

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

function mockApi(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/support-access") && method === "GET") return json(200, inbox);
      if (url.includes("/support-access/g-1/approve") && method === "POST") {
        inbox = [
          grant({ status: "approved", is_active: true, expires_at: "2026-06-01T14:00:00Z" }),
        ];
        return json(200, inbox[0]);
      }
      if (url.includes("/support-access/g-1/deny") && method === "POST") {
        inbox = [grant({ status: "denied" })];
        return json(200, inbox[0]);
      }
      return json(404, {});
    }) as unknown as typeof fetch,
  );
}

function renderInbox() {
  const value: AuthContextValue = {
    user: COMPANY_ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout: vi.fn(() => Promise.resolve()),
  };
  return render(
    <AuthContext.Provider value={value}>
      <SupportInbox />
    </AuthContext.Provider>,
  );
}

beforeEach(() => {
  inbox = [grant({})];
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SupportInbox", () => {
  it("test_shows_pending_request_with_requester_and_reason", async () => {
    mockApi();

    renderInbox();

    expect(await screen.findByText(/staff@ethera.ai requests access/)).toBeInTheDocument();
    expect(screen.getByText(/Investigate failed sync/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("test_approve_flips_to_active_with_revoke", async () => {
    const user = userEvent.setup();
    mockApi();

    renderInbox();
    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText(/has access/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke access" })).toBeInTheDocument();
  });
});
