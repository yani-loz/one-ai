/**
 * Tests for the Tier-3 controller hook — the test/sync/disconnect actions hit the right endpoints
 * and silently reload, a 401 logs out, and (the key Tier-3 invariant) a 403 from the lists is NOT a
 * logout (it's a policy denial surfaced via the allowed-types, never a forced sign-out). Driven
 * through a tiny harness component; global fetch is mocked at the boundary.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { useMyConnections } from "./useMyConnections";
import type { Connection } from "./types";

interface Call {
  url: string;
  method: string;
}

let calls: Call[];

function json(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

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

const CONNECTION: Connection = {
  id: "c-1",
  org_id: "org-1",
  connector_type: "imap",
  display_name: "My mailbox",
  auth_method: "app_password",
  username: "me@acme.de",
  host: "imap.acme.de",
  port: 993,
  use_ssl: true,
  status: "connected",
  is_enabled: true,
  disabled_at: null,
  last_checked_at: null,
  last_error: null,
  created_at: "2026-06-07T09:00:00Z",
  sync_status: "idle",
  synced_count: 0,
  total_count: null,
  last_synced_at: null,
  last_sync_error: null,
};

const ALLOWED = [{ connector_type: "imap", allowed: true, reason: null }];

/** A harness exposing the controller's lists + action buttons so a test can drive it. */
function Harness({ logout }: { logout: () => Promise<void> }): React.JSX.Element {
  const controller = useMyConnections(logout);
  const connection = controller.connections[0];
  return (
    <div>
      <span data-testid="state">{controller.loadState}</span>
      <span data-testid="count">{controller.connections.length}</span>
      <span data-testid="notice">{controller.notice ?? ""}</span>
      {connection !== undefined && (
        <>
          <button type="button" onClick={() => void controller.test(connection)}>
            do-test
          </button>
          <button type="button" onClick={() => void controller.sync(connection)}>
            do-sync
          </button>
          <button type="button" onClick={() => void controller.disconnect(connection)}>
            do-disconnect
          </button>
        </>
      )}
    </div>
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("useMyConnections", () => {
  it("test_loads_allowed_types_and_connections_on_mount", async () => {
    installFetch((call) => (call.url.includes("/types") ? json(200, ALLOWED) : json(200, [CONNECTION])));

    render(<Harness logout={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("loaded"));
    expect(screen.getByTestId("count").textContent).toBe("1");
  });

  it("test_test_action_posts_to_test_endpoint", async () => {
    installFetch((call) => {
      if (call.url.includes("/types")) return json(200, ALLOWED);
      if (call.url.includes("/test")) return json(200, CONNECTION);
      return json(200, [CONNECTION]);
    });

    render(<Harness logout={vi.fn()} />);
    fireEvent.click(await screen.findByText("do-test"));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/me/connectors/c-1/test") && c.method === "POST")).toBe(true),
    );
  });

  it("test_sync_409_is_a_noop_not_an_error_notice", async () => {
    installFetch((call) => {
      if (call.url.includes("/types")) return json(200, ALLOWED);
      if (call.url.includes("/sync") && call.method === "POST") return json(409, { detail: "running" });
      return json(200, [CONNECTION]);
    });

    render(<Harness logout={vi.fn()} />);
    fireEvent.click(await screen.findByText("do-sync"));

    // A 409 on sync is swallowed (already running) — no error notice surfaces.
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/sync") && c.method === "POST")).toBe(true),
    );
    expect(screen.getByTestId("notice").textContent).toBe("");
  });

  it("test_disconnect_calls_delete", async () => {
    installFetch((call) => {
      if (call.url.includes("/types")) return json(200, ALLOWED);
      if (call.method === "DELETE") return json(204, {});
      return json(200, [CONNECTION]);
    });

    render(<Harness logout={vi.fn()} />);
    fireEvent.click(await screen.findByText("do-disconnect"));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url.includes("/me/connectors/c-1"))).toBe(true),
    );
  });

  it("test_401_on_load_logs_out", async () => {
    const logout = vi.fn(() => Promise.resolve());
    installFetch(() => json(401, {}));

    render(<Harness logout={logout} />);

    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it("test_403_on_load_is_not_a_logout", async () => {
    // Tier-3 invariant: a 403 is a policy denial, NOT a forced sign-out (unlike the admin plane).
    const logout = vi.fn(() => Promise.resolve());
    installFetch(() => json(403, {}));

    render(<Harness logout={logout} />);

    await waitFor(() => expect(screen.getByTestId("state").textContent).toBe("error"));
    expect(logout).not.toHaveBeenCalled();
  });
});
