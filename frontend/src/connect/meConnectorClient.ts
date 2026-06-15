/**
 * Role: Thin HTTP client for the Tier-3 self-connect plane (/me/connectors/*) — any authenticated
 *       user lists the connector types they may use, lists/creates/tests/syncs/disconnects THEIR
 *       OWN connections, via the shared authorizedFetch (Bearer attach + one refresh-on-401).
 * Used by: useMyConnections (panel + detail) and the self-connect flow (ConsentModal → create).
 * Depends on: ../api/apiBase (getApiBaseUrl), ../identity (authorizedFetch, AuthRequestError),
 *             ./types, import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - Every call is OWNER-scoped server-side: the backend derives the owner from the JWT, so another
 *     user's connection id is a 404 (never an existence leak); org scope is never sent.
 *   - self-connect (POST /me/connectors) carries the GDPR Art. 7 consent block; the backend rejects
 *     it (400) if consent.accepted is not true. The password is write-only — no response returns it.
 *   - disconnect hits DELETE (204, NO body) — it erases the connection + its ingested data; success
 *     is the 2xx alone, never a parsed body. There is NO enable/disable in this plane (that is admin
 *     governance); a user connects or disconnects+erases.
 */
import { getApiBaseUrl } from "../api/apiBase";
import { authorizedFetch, AuthRequestError } from "../identity";
import type {
  AllowedConnectorType,
  Connection,
  SelfConnectRequest,
  SyncStatus,
} from "./types";

const API_URL = getApiBaseUrl();

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/** List which connector types the caller may self-connect (drives the panel cards). */
export async function listAllowedTypes(): Promise<AllowedConnectorType[]> {
  return parseJsonOrThrow<AllowedConnectorType[]>(
    await authorizedFetch(`${API_URL}/me/connectors/types`),
  );
}

/** List the caller's OWN connections (newest-first, metadata only). */
export async function listMyConnections(): Promise<Connection[]> {
  return parseJsonOrThrow<Connection[]>(await authorizedFetch(`${API_URL}/me/connectors`));
}

/**
 * Self-connect the caller's OWN mailbox via POST /me/connectors (credential encrypted server-side).
 *
 * Contract: returns the created connection (201). Throws AuthRequestError — 403 when the user isn't
 * allowed to self-connect this type, 400 when consent is missing/not accepted, 409 when the user
 * already connected this mailbox, 503 when the server's connector key is mis-set.
 */
export async function selfConnect(payload: SelfConnectRequest): Promise<Connection> {
  const response = await authorizedFetch(`${API_URL}/me/connectors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<Connection>(response);
}

/** Verify one of the caller's OWN connections; returns its updated status (404 if not theirs). */
export async function testMyConnection(connectionId: string): Promise<Connection> {
  return parseJsonOrThrow<Connection>(
    await authorizedFetch(`${API_URL}/me/connectors/${connectionId}/test`, { method: "POST" }),
  );
}

/**
 * Trigger an incremental sync of the caller's OWN connection (POST → 202) and return its status.
 *
 * Contract: throws AuthRequestError — 409 when a sync is already running, 404 if not theirs (the
 * caller treats 409 as "already in progress", not a hard error).
 */
export async function startMySync(connectionId: string): Promise<SyncStatus> {
  return parseJsonOrThrow<SyncStatus>(
    await authorizedFetch(`${API_URL}/me/connectors/${connectionId}/sync`, { method: "POST" }),
  );
}

/** Poll the caller's OWN connection's live sync progress (GET, 404 if not theirs). */
export async function getMySyncStatus(connectionId: string): Promise<SyncStatus> {
  return parseJsonOrThrow<SyncStatus>(
    await authorizedFetch(`${API_URL}/me/connectors/${connectionId}/sync`),
  );
}

/**
 * Disconnect + erase the caller's OWN connection via DELETE /me/connectors/{id} (204, no body).
 *
 * Contract: resolves on a 2xx (the endpoint returns 204 — there is NO body to parse). This ALSO
 * erases the connection's ingested email/attachments/sync state + consent (GDPR Art. 17 raw-tier).
 * Throws AuthRequestError — 404 unknown/foreign connection, 401 lapsed session.
 */
export async function disconnectMyConnection(connectionId: string): Promise<void> {
  const response = await authorizedFetch(`${API_URL}/me/connectors/${connectionId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
}
