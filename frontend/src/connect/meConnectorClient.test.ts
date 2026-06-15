/**
 * Unit tests for the Tier-3 self-connect HTTP client — allowed-types / list / self-connect / test /
 * sync / disconnect against a mocked global fetch (the adapter boundary). localStorage is cleared so
 * a 401 has no refresh token to rotate and surfaces cleanly as AuthRequestError. Asserts the consent
 * block rides the self-connect POST and that disconnect (DELETE 204) never parses a body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  disconnectMyConnection,
  listAllowedTypes,
  listMyConnections,
  selfConnect,
  startMySync,
  testMyConnection,
} from "./meConnectorClient";
import type { SelfConnectRequest } from "./types";

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

const SAMPLE_CONNECTION = {
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

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("listAllowedTypes", () => {
  it("test_returns_parsed_allowed_types", async () => {
    mockFetch(() => jsonResponse(200, [{ connector_type: "imap", allowed: true, reason: null }]));

    const types = await listAllowedTypes();

    expect(types[0]?.allowed).toBe(true);
    expect(calls[0]?.url).toContain("/me/connectors/types");
  });
});

describe("listMyConnections", () => {
  it("test_lists_own_connections", async () => {
    mockFetch(() => jsonResponse(200, [SAMPLE_CONNECTION]));

    const connections = await listMyConnections();

    expect(connections).toHaveLength(1);
    expect(calls[0]?.url).toMatch(/\/me\/connectors$/);
  });

  it("test_surfaces_401_as_auth_error", async () => {
    mockFetch(() => jsonResponse(401, {}));

    await expect(listMyConnections()).rejects.toMatchObject({ status: 401 });
  });
});

describe("selfConnect", () => {
  const payload: SelfConnectRequest = {
    connector_type: "imap",
    display_name: "My mailbox",
    host: "imap.acme.de",
    port: 993,
    use_ssl: true,
    username: "me@acme.de",
    password: "app-pass",
    consent: { accepted: true, scope: "mailbox:read", consent_version: "v1" },
  };

  it("test_posts_payload_with_consent_block", async () => {
    mockFetch(() => jsonResponse(201, SAMPLE_CONNECTION));

    const created = await selfConnect(payload);

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toMatch(/\/me\/connectors$/);
    expect(calls[0]?.body?.consent).toMatchObject({ accepted: true, scope: "mailbox:read" });
    expect(created.id).toBe("c-1");
  });

  it("test_throws_403_when_not_allowed", async () => {
    mockFetch(() => jsonResponse(403, { detail: "denied" }));

    await expect(selfConnect(payload)).rejects.toMatchObject({ status: 403 });
  });

  it("test_throws_409_on_already_connected", async () => {
    mockFetch(() => jsonResponse(409, { detail: "exists" }));

    await expect(selfConnect(payload)).rejects.toMatchObject({ status: 409 });
  });
});

describe("testMyConnection", () => {
  it("test_posts_to_the_test_endpoint", async () => {
    mockFetch(() => jsonResponse(200, SAMPLE_CONNECTION));

    await testMyConnection("c-1");

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/me/connectors/c-1/test");
  });
});

describe("startMySync", () => {
  it("test_posts_to_the_sync_endpoint", async () => {
    mockFetch(() =>
      jsonResponse(202, {
        connection_id: "c-1",
        sync_status: "running",
        run_id: "r-1",
        started_at: "2026-06-07T12:00:00Z",
        last_synced_at: null,
        synced_count: 0,
        total_count: 3,
        last_sync_error: null,
      }),
    );

    const status = await startMySync("c-1");

    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.url).toContain("/me/connectors/c-1/sync");
    expect(status.sync_status).toBe("running");
  });

  it("test_throws_409_when_already_running", async () => {
    mockFetch(() => jsonResponse(409, { detail: "running" }));

    await expect(startMySync("c-1")).rejects.toMatchObject({ status: 409 });
  });
});

describe("disconnectMyConnection", () => {
  it("test_sends_delete_and_resolves_on_204_without_parsing", async () => {
    mockFetch(
      () =>
        ({
          ok: true,
          status: 204,
          json: () => Promise.reject(new Error("204 No Content has no body — do not parse")),
        }) as Response,
    );

    await expect(disconnectMyConnection("c-1")).resolves.toBeUndefined();

    expect(calls[0]?.method).toBe("DELETE");
    expect(calls[0]?.url).toContain("/me/connectors/c-1");
  });

  it("test_throws_404_on_foreign_connection", async () => {
    mockFetch(() => jsonResponse(404, {}));

    await expect(disconnectMyConnection("other")).rejects.toMatchObject({ status: 404 });
  });
});
