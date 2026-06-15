/**
 * Tests for the Tier-3 "My Connections" panel — the allowed-types list drives the cards (allowed →
 * an interactive card linking to detail; not-allowed → a locked card with its reason), and an
 * existing connection's health summary renders on its card. Global fetch is mocked at the boundary
 * (branching on URL); the session is injected via a controlled AuthContext (a plain member).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { MyConnectionsPage } from "./MyConnectionsPage";

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

/** Install a fetch mock: /types and /me/connectors each return their own payload by URL. */
function installFetch(types: unknown, connections: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/me/connectors/types")) return Promise.resolve(json(200, types));
      if (url.includes("/me/connectors")) return Promise.resolve(json(200, connections));
      return Promise.resolve(json(404, {}));
    }) as unknown as typeof fetch,
  );
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
  synced_count: 42,
  total_count: null,
  last_synced_at: "2026-06-07T11:00:00Z",
  last_sync_error: null,
};

function renderPage(logout = vi.fn()) {
  const value: AuthContextValue = {
    user: MEMBER,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter>
        <MyConnectionsPage />
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

describe("MyConnectionsPage", () => {
  it("test_allowed_type_renders_an_interactive_card_linking_to_detail", async () => {
    installFetch([{ connector_type: "imap", allowed: true, reason: null }], []);

    renderPage();

    const card = await screen.findByRole("link", { name: /Email/ });
    expect(card).toHaveAttribute("href", "/connections/imap");
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });

  it("test_connected_card_shows_records_synced_health", async () => {
    installFetch([{ connector_type: "imap", allowed: true, reason: null }], [CONNECTION]);

    renderPage();

    expect(await screen.findByText(/42 messages synced/)).toBeInTheDocument();
  });

  it("test_not_allowed_type_renders_locked_with_its_reason_and_no_link", async () => {
    installFetch(
      [
        {
          connector_type: "imap",
          allowed: false,
          reason: "Your administrator hasn't enabled this",
        },
      ],
      [],
    );

    renderPage();

    expect(
      await screen.findByText(/Your administrator hasn't enabled this/),
    ).toBeInTheDocument();
    // A not-allowed card is non-interactive — no link to a detail the user can't use.
    expect(screen.queryByRole("link", { name: /Email/ })).not.toBeInTheDocument();
  });

  it("test_empty_allowed_types_shows_no_connectors_hint", async () => {
    installFetch([], []);

    renderPage();

    expect(
      await screen.findByText(/No connectors are available to you yet/),
    ).toBeInTheDocument();
  });
});
