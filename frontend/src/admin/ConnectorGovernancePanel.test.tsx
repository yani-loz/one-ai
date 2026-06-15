/**
 * Tests for the Tier-2 connector governance panel — the org-wide enable/disable toggle (PUT
 * /policies), the per-user grant override (PUT /overrides), the can't-exceed-entitlement disabled
 * state (entitled=false locks the toggle + grants), and the §7 metadata-only render (a connection's
 * health shows; never a mailbox/secret). Global fetch is mocked at the boundary (branching on URL +
 * method); the panel pulls the user list from /users too. Session = a controlled company-admin.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { ConnectorGovernancePanel } from "./ConnectorGovernancePanel";

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: Call[];

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

const USERS = [
  {
    id: "u-2",
    email: "bob@acme.de",
    full_name: "Bob Member",
    role: "member",
    is_active: true,
    org_id: "org-1",
    created_at: "2026-06-01T10:00:00Z",
  },
];

/** A §7 metadata-only connection row (owner + health, NO mailbox/secret). */
const CONNECTION_META = {
  id: "c-1",
  connector_type: "imap",
  owner_user_id: "u-2",
  status: "connected",
  is_enabled: true,
  sync_status: "idle",
  synced_count: 7,
  total_count: null,
  last_synced_at: "2026-06-07T11:00:00Z",
  last_error: null,
};

/** Build the governance view payload with overridable fields. */
function governance(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    connector_type: "imap",
    entitled: true,
    org_wide_enabled: false,
    overrides: [],
    connections: [CONNECTION_META],
    ...overrides,
  };
}

/**
 * Install a fetch mock. `gov` is the governance GET payload; a PUT /policies flips org_wide_enabled,
 * a PUT /overrides adds the grant — both echo the refreshed view (like the API).
 */
function installFetch(gov: Record<string, unknown>): void {
  calls = [];
  let current = { ...gov };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(call);
      if (call.url.includes("/users")) return Promise.resolve(json(200, USERS));
      if (call.url.includes("/governance/")) return Promise.resolve(json(200, current));
      if (call.url.includes("/policies") && call.method === "PUT") {
        current = { ...current, org_wide_enabled: call.body?.org_wide_enabled };
        return Promise.resolve(json(200, current));
      }
      if (call.url.includes("/overrides") && call.method === "PUT") {
        current = {
          ...current,
          overrides: [
            { user_id: call.body?.user_id, connector_type: "imap", override_type: call.body?.override_type },
          ],
        };
        return Promise.resolve(json(200, current));
      }
      return Promise.resolve(json(404, {}));
    }) as unknown as typeof fetch,
  );
}

function renderPanel(logout = vi.fn()) {
  const value: AuthContextValue = {
    user: ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  render(
    <AuthContext.Provider value={value}>
      <ConnectorGovernancePanel connectorType="imap" />
    </AuthContext.Provider>,
  );
  return { logout };
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ConnectorGovernancePanel", () => {
  it("test_org_wide_toggle_puts_policy_and_flips_label", async () => {
    const user = userEvent.setup();
    installFetch(governance());

    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Enable org-wide" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.includes("/admin/connectors/policies") &&
            call.method === "PUT" &&
            call.body?.org_wide_enabled === true,
        ),
      ).toBe(true),
    );
    expect(await screen.findByRole("button", { name: "Disable org-wide" })).toBeInTheDocument();
  });

  it("test_per_user_grant_puts_override", async () => {
    const user = userEvent.setup();
    installFetch(governance());

    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.includes("/admin/connectors/overrides") &&
            call.method === "PUT" &&
            call.body?.override_type === "grant" &&
            call.body?.user_id === "u-2",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Granted")).toBeInTheDocument();
  });

  it("test_not_entitled_disables_org_wide_toggle_and_grant", async () => {
    installFetch(governance({ entitled: false }));

    renderPanel();

    // The Tier-1 ceiling: an admin can't enable a type beyond the plan.
    expect(await screen.findByRole("button", { name: "Enable org-wide" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "Grant" })).toBeDisabled();
    expect(screen.getByText("Not in plan")).toBeInTheDocument();
  });

  it("test_render_is_metadata_only_no_mailbox_or_secret", async () => {
    installFetch(governance());

    renderPanel();

    // §7: the health roll-up shows the user + sync state, never a mailbox address or secret.
    expect(await screen.findByText("Bob Member")).toBeInTheDocument();
    expect(screen.getByText(/Connected · 7 synced/)).toBeInTheDocument();
    expect(screen.queryByText(/bob@acme\.de/)).not.toBeInTheDocument();
    expect(screen.queryByText(/imap\.acme/)).not.toBeInTheDocument();
  });
});
