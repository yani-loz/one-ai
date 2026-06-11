/**
 * Role: Shared types for the Connect (connectors) feature — a stored connection as the backend
 *       returns it, and the create-connection request body.
 * Used by: connectClient, useConnectors, ConnectorsPage, AddMailboxDrawer.
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
