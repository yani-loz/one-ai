/**
 * Tests for the Tier-1 connector entitlement panel — the toggle reflects the company's current
 * entitlement (a missing row reads as "not granted"), Grant PUTs enabled=true and flips to "Revoke",
 * and Revoke PUTs enabled=false. Global fetch is mocked at the boundary (branching on URL + method);
 * the panel acts on the {orgId} prop (platform-admin cross-org), and reports 401 via onAuthExpired.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConnectorEntitlementsPanel } from "./ConnectorEntitlementsPanel";

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

/** A persisted entitlement row for IMAP at a given enabled state. */
function imapRow(enabled: boolean): Record<string, unknown> {
  return {
    org_id: "org-1",
    connector_type: "imap",
    enabled,
    granted_at: "2026-06-01T10:00:00Z",
    revoked_at: enabled ? null : "2026-06-02T10:00:00Z",
  };
}

/** Install a fetch mock seeded with the initial GET list; PUT echoes the requested row. */
function installFetch(initial: unknown): void {
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
      if (call.method === "PUT") {
        return Promise.resolve(json(200, imapRow(Boolean(call.body?.enabled))));
      }
      return Promise.resolve(json(200, initial));
    }) as unknown as typeof fetch,
  );
}

function renderPanel(onAuthExpired = vi.fn()) {
  render(<ConnectorEntitlementsPanel orgId="org-1" onAuthExpired={onAuthExpired} />);
  return { onAuthExpired };
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ConnectorEntitlementsPanel", () => {
  it("test_missing_row_reads_as_not_granted_and_offers_grant", async () => {
    installFetch([]); // no entitlement rows yet

    renderPanel();

    expect(await screen.findByRole("button", { name: "Grant" })).toBeInTheDocument();
    expect(screen.getByText(/Not included in this company's plan/)).toBeInTheDocument();
  });

  it("test_grant_puts_enabled_true_then_flips_to_revoke", async () => {
    const user = userEvent.setup();
    installFetch([]);

    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.includes("/connector-entitlements") &&
            call.method === "PUT" &&
            call.body?.connector_type === "imap" &&
            call.body?.enabled === true,
        ),
      ).toBe(true),
    );
    expect(await screen.findByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("test_revoke_puts_enabled_false_for_an_entitled_company", async () => {
    const user = userEvent.setup();
    installFetch([imapRow(true)]);

    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    await waitFor(() =>
      expect(
        calls.some(
          (call) => call.method === "PUT" && call.body?.enabled === false,
        ),
      ).toBe(true),
    );
    expect(await screen.findByRole("button", { name: "Grant" })).toBeInTheDocument();
  });

  it("test_401_on_load_calls_on_auth_expired", async () => {
    const onAuthExpired = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(json(401, {}))) as unknown as typeof fetch,
    );

    renderPanel(onAuthExpired);

    await waitFor(() => expect(onAuthExpired).toHaveBeenCalled());
  });
});
