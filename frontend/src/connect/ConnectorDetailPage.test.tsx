/**
 * Tests for the Tier-3 connector detail screen — the tabs render the caller's own connection, the
 * Connection tab gates the first connect behind the consent modal (no POST until consent + a valid
 * form), a connected mailbox can be disconnected+erased, and a not-allowed :type shows the friendly
 * denial (never another user's data). Global fetch is mocked at the boundary (branching on URL +
 * method); the session is a controlled AuthContext (a member). Routed at /connections/:type.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { ConnectorDetailPage } from "./ConnectorDetailPage";

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

const MEMBER: AuthUser = {
  id: "u-1",
  email: "bob@acme.de",
  full_name: "Bob Member",
  role: "member",
  org_id: "org-1",
  org_name: "Acme GmbH",
};

const CONNECTION = {
  id: "c-1",
  org_id: "org-1",
  connector_type: "imap",
  display_name: "Bob's mailbox",
  auth_method: "app_password",
  username: "bob@acme.de",
  host: "imap.acme.de",
  port: 993,
  use_ssl: true,
  status: "connected",
  is_enabled: true,
  disabled_at: null,
  last_checked_at: "2026-06-07T10:00:00Z",
  last_error: null,
  created_at: "2026-06-07T09:00:00Z",
  sync_status: "idle",
  synced_count: 10,
  total_count: null,
  last_synced_at: "2026-06-07T11:00:00Z",
  last_sync_error: null,
};

/** Install a fetch mock with caller-supplied handlers for the allowed-types + connections lists. */
function installFetch(handler: (call: Call) => Response): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(call);
      return Promise.resolve(handler(call));
    }) as unknown as typeof fetch,
  );
}

const ALLOWED = [{ connector_type: "imap", allowed: true, reason: null }];

function renderDetail(type = "imap", logout = vi.fn()) {
  const value: AuthContextValue = {
    user: MEMBER,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={[`/connections/${type}`]}>
        <Routes>
          <Route path="/connections/:type" element={<ConnectorDetailPage />} />
          <Route path="/connections" element={<div>PANEL</div>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
  return { logout };
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ConnectorDetailPage", () => {
  it("test_status_tab_shows_connected_health_for_own_connection", async () => {
    installFetch((call) =>
      call.url.includes("/types") ? json(200, ALLOWED) : json(200, [CONNECTION]),
    );

    renderDetail();

    expect(await screen.findByRole("heading", { name: "Email" })).toBeInTheDocument();
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
  });

  it("test_connect_requires_consent_then_posts_self_connect_with_consent_block", async () => {
    const user = userEvent.setup();
    // No connection yet → the Connection tab offers "Connect my mailbox".
    installFetch((call) => {
      if (call.url.includes("/types")) return json(200, ALLOWED);
      if (call.method === "POST" && call.url.endsWith("/me/connectors")) {
        return json(201, CONNECTION);
      }
      if (call.url.includes("/test")) return json(200, CONNECTION);
      return json(200, []); // empty connection list
    });

    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Connection" }));
    await user.click(await screen.findByRole("button", { name: "Connect my mailbox" }));

    // The submit button is gated until consent is ticked AND the form is valid.
    const submit = await screen.findByRole("button", { name: "Connect mailbox" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByLabelText(/I consent to One AI accessing this mailbox/));
    await user.type(screen.getByLabelText("Email address"), "bob@acme.de");
    await user.type(screen.getByLabelText("App password"), "app-pass-1234");
    expect(submit).toBeEnabled();

    await user.click(submit);

    // The POST carried consent.accepted=true — the HITL gate is recorded.
    await waitFor(() => {
      const post = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/me/connectors"),
      );
      expect(post).toBeDefined();
      const consent = post?.body?.consent as { accepted?: boolean } | undefined;
      expect(consent?.accepted).toBe(true);
    });
    expect(await screen.findByText("✓ Connected")).toBeInTheDocument();
  });

  it("test_disconnect_and_erase_calls_delete_after_confirm", async () => {
    const user = userEvent.setup();
    installFetch((call) => {
      if (call.url.includes("/types")) return json(200, ALLOWED);
      if (call.method === "DELETE") return json(204, {});
      return json(200, [CONNECTION]);
    });

    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Connection" }));
    await user.click(await screen.findByRole("button", { name: "Disconnect & erase" }));
    await user.click(await screen.findByRole("button", { name: "Yes, disconnect & erase" }));

    await waitFor(() =>
      expect(
        calls.some((call) => call.method === "DELETE" && call.url.includes("/me/connectors/c-1")),
      ).toBe(true),
    );
  });

  it("test_not_allowed_type_shows_friendly_denial_not_another_users_data", async () => {
    installFetch((call) =>
      call.url.includes("/types")
        ? json(200, [
            {
              connector_type: "imap",
              allowed: false,
              reason: "Your administrator hasn't enabled this",
            },
          ])
        : json(200, []),
    );

    renderDetail();

    expect(
      await screen.findByText(/Your administrator hasn't enabled this/),
    ).toBeInTheDocument();
    // No tabs render for a denied type — the user can't act on it.
    expect(screen.queryByRole("button", { name: "Connection" })).not.toBeInTheDocument();
  });
});
