/**
 * Tests for the Connect console page — load/empty rendering and the disable action. Global fetch
 * is mocked at the boundary (branching on URL + method); the session is injected via a controlled
 * AuthContext (a company_admin).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../identity/authContext";
import type { AuthUser } from "../identity";
import { ConnectorsPage } from "./ConnectorsPage";

interface Call {
  url: string;
  method: string;
}

let calls: Call[];

function installFetch(handler: (call: Call) => Response): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = { url: String(input), method: init?.method ?? "GET" };
      calls.push(call);
      return Promise.resolve(handler(call));
    }) as unknown as typeof fetch,
  );
}

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

const CONNECTION = {
  id: "c-1",
  org_id: "org-1",
  connector_type: "imap",
  display_name: "Sales mailbox",
  auth_method: "app_password",
  username: "sales@acme.de",
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
  synced_count: 0,
  total_count: null,
  last_synced_at: null,
  last_sync_error: null,
};

function renderPage(logout = vi.fn()) {
  const value: AuthContextValue = {
    user: ADMIN,
    status: "authenticated",
    login: vi.fn(),
    platformLogin: vi.fn(),
    logout,
  };
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter>
        <ConnectorsPage />
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

describe("ConnectorsPage", () => {
  it("test_renders_connections_after_load", async () => {
    installFetch(() => json(200, [CONNECTION]));
    renderPage();

    expect(await screen.findByText("Sales mailbox")).toBeInTheDocument();
    expect(screen.getByText("sales@acme.de")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("test_empty_shows_connect_first_hint", async () => {
    installFetch(() => json(200, []));
    renderPage();

    expect(await screen.findByText(/No mailboxes connected yet/i)).toBeInTheDocument();
  });

  it("test_sync_button_posts_to_sync_then_shows_live_progress", async () => {
    let started = false; // the initial list load is idle; only after the POST does it report running
    installFetch((call) => {
      if (call.url.includes("/sync") && call.method === "POST") {
        started = true;
        return json(202, {
          connection_id: "c-1",
          sync_status: "running",
          run_id: "r-1",
          started_at: "2026-06-07T12:00:00Z",
          last_synced_at: null,
          synced_count: 0,
          total_count: 3,
          last_sync_error: null,
        });
      }
      return json(200, [
        started
          ? { ...CONNECTION, sync_status: "running", synced_count: 1, total_count: 3 }
          : CONNECTION,
      ]);
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Sync now" }));

    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes("/connectors/c-1/sync") && call.method === "POST"),
      ).toBe(true),
    );
    expect(await screen.findByText(/Syncing… 1 \/ 3 messages/)).toBeInTheDocument();
  });

  it("test_sync_button_is_disabled_for_a_disabled_connection", async () => {
    installFetch(() => json(200, [{ ...CONNECTION, is_enabled: false, disabled_at: "2026-06-07T11:00:00Z" }]));
    renderPage();

    expect(await screen.findByRole("button", { name: "Sync now" })).toBeDisabled();
  });

  it("test_disable_button_calls_the_disable_endpoint", async () => {
    installFetch((call) =>
      call.url.includes("/disable")
        ? json(200, { ...CONNECTION, is_enabled: false, disabled_at: "2026-06-07T11:00:00Z" })
        : json(200, [CONNECTION]),
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Disable" }));

    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes("/connectors/c-1/disable") && call.method === "POST"),
      ).toBe(true),
    );
  });
});
