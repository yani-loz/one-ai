/**
 * Unit tests for the connectors HTTP client — list / create / test / enable / disable / delete
 * against a mocked global fetch (the adapter boundary). localStorage is cleared so a 401 has no
 * refresh token to rotate and surfaces cleanly as AuthRequestError.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createConnection,
  deleteConnection,
  disableConnection,
  enableConnection,
  listConnections,
  startSync,
  testConnection,
} from "./connectClient";
import type { CreateConnectionRequest } from "./types";

interface Recorded {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: Recorded[];

function mockFetch(handler: (recorded: Recorded) => Response): void {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const recorded: Recorded = {
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(recorded);
      return Promise.resolve(handler(recorded));
    }) as unknown as typeof fetch,
  );
}

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

const SAMPLE = {
  id: "c-1",
  org_id: "org-1",
  connector_type: "imap",
  display_name: "Sales mailbox",
  auth_method: "app_password",
  username: "sales@acme.de",
  host: "imap.acme.de",
  port: 993,
  use_ssl: true,
  status: "configured",
  is_enabled: true,
  disabled_at: null,
  last_checked_at: null,
  last_error: null,
  created_at: "2026-06-07T10:00:00Z",
  sync_status: "idle",
  synced_count: 0,
  total_count: null,
  last_synced_at: null,
  last_sync_error: null,
};

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("listConnections", () => {
  it("test_list_returns_parsed_connections_on_200", async () => {
    mockFetch(() => jsonResponse(200, [SAMPLE]));

    const connections = await listConnections();

    expect(connections).toHaveLength(1);
    expect(connections[0]?.username).toBe("sales@acme.de");
    expect(calls[0]?.url).toContain("/connectors");
    expect(calls[0]?.method).toBe("GET");
  });

  it("test_list_surfaces_401_as_auth_error", async () => {
    mockFetch(() => jsonResponse(401, {}));

    await expect(listConnections()).rejects.toMatchObject({ status: 401 });
  });
});

describe("createConnection", () => {
  const payload: CreateConnectionRequest = {
    connector_type: "imap",
    display_name: "Sales mailbox",
    host: "imap.acme.de",
    port: 993,
    use_ssl: true,
    username: "sales@acme.de",
    password: "app-pw-123",
  };

  it("test_create_posts_payload_and_returns_connection", async () => {
    mockFetch(() => jsonResponse(201, SAMPLE));

    const created = await createConnection(payload);

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/connectors");
    expect(calls[0]?.body).toMatchObject({ username: "sales@acme.de", connector_type: "imap" });
    expect(created.id).toBe("c-1");
  });

  it("test_create_throws_409_on_duplicate_mailbox", async () => {
    mockFetch(() => jsonResponse(409, { detail: "exists" }));

    await expect(createConnection(payload)).rejects.toMatchObject({ status: 409 });
  });
});

describe("test/enable/disable", () => {
  it("test_test_posts_to_the_test_endpoint", async () => {
    mockFetch(() => jsonResponse(200, { ...SAMPLE, status: "connected" }));

    const checked = await testConnection("c-1");

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/connectors/c-1/test");
    expect(checked.status).toBe("connected");
  });

  it("test_disable_sets_is_enabled_false", async () => {
    mockFetch(() =>
      jsonResponse(200, { ...SAMPLE, is_enabled: false, disabled_at: "2026-06-07T11:00:00Z" }),
    );

    const disabled = await disableConnection("c-1");

    expect(calls[0]?.url).toContain("/connectors/c-1/disable");
    expect(disabled.is_enabled).toBe(false);
  });

  it("test_enable_clears_disabled", async () => {
    mockFetch(() => jsonResponse(200, { ...SAMPLE, is_enabled: true, disabled_at: null }));

    const enabled = await enableConnection("c-1");

    expect(calls[0]?.url).toContain("/connectors/c-1/enable");
    expect(enabled.is_enabled).toBe(true);
  });
});

describe("startSync", () => {
  const running = {
    connection_id: "c-1",
    sync_status: "running",
    run_id: "r-1",
    started_at: "2026-06-07T12:00:00Z",
    last_synced_at: null,
    synced_count: 0,
    total_count: 12,
    last_sync_error: null,
  };

  it("test_start_sync_posts_to_the_sync_endpoint_and_returns_running", async () => {
    mockFetch(() => jsonResponse(202, running));

    const status = await startSync("c-1");

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/connectors/c-1/sync");
    expect(status.sync_status).toBe("running");
    expect(status.total_count).toBe(12);
  });

  it("test_start_sync_throws_409_when_already_running", async () => {
    mockFetch(() => jsonResponse(409, { detail: "A sync is already running for this connection." }));

    await expect(startSync("c-1")).rejects.toMatchObject({ status: 409 });
  });
});

describe("deleteConnection", () => {
  it("test_delete_sends_delete_and_resolves_on_204_without_parsing", async () => {
    mockFetch(
      () =>
        ({
          ok: true,
          status: 204,
          json: () => Promise.reject(new Error("204 No Content has no body — do not parse")),
        }) as Response,
    );

    await expect(deleteConnection("c-1")).resolves.toBeUndefined();
    expect(calls[0]?.method).toBe("DELETE");
    expect(calls[0]?.url).toContain("/connectors/c-1");
  });

  it("test_delete_throws_404_on_unknown_connection", async () => {
    mockFetch(() => jsonResponse(404, {}));

    await expect(deleteConnection("nope")).rejects.toMatchObject({ status: 404 });
  });
});
