/**
 * Role: Shared types for the Connect (connectors) feature — a stored connection as the backend
 *       returns it, the admin create-connection request, and the Tier-3 self-connect shapes
 *       (allowed-type, consent, self-connect request).
 * Used by: connectClient, useConnectors, ConnectorsPage, AddMailboxDrawer (admin plane);
 *          meConnectorClient, useMyConnections, MyConnectionsPage, ConnectorDetailPage,
 *          ConnectorCard, ConsentModal (Tier-3 self-connect plane).
 * Depends on: nothing (leaf).
 * Key invariants:
 *   - `Connection` mirrors the backend ConnectionResponse EXACTLY (no secret field exists there).
 *   - `status` is verification health (configured | connected | error); `is_enabled` is the
 *     separate admin on/off (disabled_at !== null ⇒ is_enabled false). They are orthogonal.
 *   - `sync_status` (idle | running | error) is a THIRD orthogonal axis — the live sync state the
 *     list carries so the UI shows N/M (synced_count / total_count) without a second call.
 */

/** A connector connection as returned by the backend (metadata only — never a secret). */
export interface Connection {
  id: string;
  org_id: string;
  connector_type: string;
  display_name: string;
  auth_method: string;
  username: string;
  host: string;
  port: number;
  use_ssl: boolean;
  status: "configured" | "connected" | "error" | string;
  is_enabled: boolean;
  disabled_at: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
  sync_status: "idle" | "running" | "error" | string;
  synced_count: number;
  total_count: number | null;
  last_synced_at: string | null;
  last_sync_error: string | null;
}

/** The focused sync-status view returned by POST/GET /connectors/{id}/sync. */
export interface SyncStatus {
  connection_id: string;
  sync_status: "idle" | "running" | "error" | string;
  run_id: string | null;
  started_at: string | null;
  last_synced_at: string | null;
  synced_count: number;
  total_count: number | null;
  last_sync_error: string | null;
}

/** Body for POST /connectors (IMAP, the only connector kind today). `password` is write-only. */
export interface CreateConnectionRequest {
  connector_type: "imap";
  display_name: string;
  host: string;
  port: number;
  use_ssl: boolean;
  username: string;
  password: string;
}

/**
 * Whether the calling user may self-connect a connector type — drives the panel cards.
 * Mirrors the backend AllowedConnectorTypeResponse (GET /me/connectors/types).
 */
export interface AllowedConnectorType {
  connector_type: string;
  allowed: boolean;
  /** Friendly denial reason when not allowed (e.g. "not in your company's plan"); null if allowed. */
  reason: string | null;
}

/**
 * The Art. 7 consent a user gives at self-connect (HITL gate). `accepted` MUST be true for the
 * backend to create the connection. Mirrors the backend ConsentInput.
 */
export interface ConsentInput {
  accepted: boolean;
  scope: string;
  consent_version: string;
}

/**
 * Body for POST /me/connectors — self-connect MY OWN mailbox (Tier 3). Identical to the admin
 * create request plus the mandatory consent block. `password` is write-only.
 */
export interface SelfConnectRequest {
  connector_type: "imap";
  display_name: string;
  host: string;
  port: number;
  use_ssl: boolean;
  username: string;
  password: string;
  consent: ConsentInput;
}
