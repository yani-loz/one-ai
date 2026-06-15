/**
 * Role: Thin HTTP client for the company-admin connectors backend (/admin/connectors/*) — list /
 *       create / test / enable / disable / delete an ORG-OWNED (shared) mailbox connection in the
 *       caller's own org, via the shared authorizedFetch (Bearer attach + one refresh-on-401). This
 *       is the legacy Tier-2 admin plane; the per-user self-connect plane is meConnectorClient.ts.
 * Used by: useConnectors (list + lifecycle) and AddMailboxDrawer (create + test).
 * Depends on: ../identity (authorizedFetch, AuthRequestError), ./types, import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - All calls go through authorizedFetch; a 2nd 401 surfaces as AuthRequestError(401) → caller
 *     logs out. org scope is NEVER sent (the backend derives org from the JWT; a cross-org id is a
 *     404, never an existence leak). The credential is write-only — no response ever returns it.
 *   - delete returns 204 with NO body — never parse JSON; success is the 2xx alone.
 *   - CO-01 MOVED these routes from /connectors/* to /admin/connectors/*; this client targets the
 *     new admin prefix.
 */
import { getApiBaseUrl } from "../api/apiBase";
import { authorizedFetch, AuthRequestError } from "../identity";
import type { Connection, CreateConnectionRequest, SyncStatus } from "./types";

const API_URL = getApiBaseUrl();

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/** List the caller's org connections (newest-first, metadata only). */
export async function listConnections(): Promise<Connection[]> {
  return parseJsonOrThrow<Connection[]>(await authorizedFetch(`${API_URL}/admin/connectors`));
}

/**
 * Create a connection via POST /connectors (credential encrypted server-side).
 *
 * Contract: returns the created connection. Throws AuthRequestError — notably 409 when this org
 * already has a connection for this mailbox, 503 when the server's connector key is mis-set.
 */
export async function createConnection(payload: CreateConnectionRequest): Promise<Connection> {
  const response = await authorizedFetch(`${API_URL}/admin/connectors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<Connection>(response);
}

/** Verify a stored connection (connect + authenticate); returns its updated status (never raises
 *  on a failed verification — that's a 200 with status='error'). */
export async function testConnection(connectionId: string): Promise<Connection> {
  return parseJsonOrThrow<Connection>(
    await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}/test`, { method: "POST" }),
  );
}

/**
 * Trigger an incremental sync (POST /connectors/{id}/sync → 202) and return the live status.
 *
 * Contract: throws AuthRequestError — notably 409 when a sync is already running OR the connection
 * is disabled (the caller treats 409 as "already in progress / not now", not a hard error).
 */
export async function startSync(connectionId: string): Promise<SyncStatus> {
  return parseJsonOrThrow<SyncStatus>(
    await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}/sync`, { method: "POST" }),
  );
}

/** Poll a connection's live sync progress (GET /connectors/{id}/sync). */
export async function getSyncStatus(connectionId: string): Promise<SyncStatus> {
  return parseJsonOrThrow<SyncStatus>(
    await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}/sync`),
  );
}

/** Disable a connection (reversible) — stops sync + revokes AI access. Returns the updated row. */
export async function disableConnection(connectionId: string): Promise<Connection> {
  return parseJsonOrThrow<Connection>(
    await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}/disable`, { method: "POST" }),
  );
}

/** Re-enable a disabled connection. Returns the updated row. */
export async function enableConnection(connectionId: string): Promise<Connection> {
  return parseJsonOrThrow<Connection>(
    await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}/enable`, { method: "POST" }),
  );
}

/** Delete a connection via DELETE /connectors/{id} (204, no body — do not parse). */
export async function deleteConnection(connectionId: string): Promise<void> {
  const response = await authorizedFetch(`${API_URL}/admin/connectors/${connectionId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
}
