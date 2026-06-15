/**
 * Role: Shared TypeScript contracts for the Tier-2 connector GOVERNANCE surface (CO-01) — mirrors the
 *       backend /admin/connectors governance shapes (ConnectorGovernanceResponse + the override row +
 *       the §7 metadata-only connection view). The admin governs reach + sees health; never a secret.
 * Used by: connectorGovernanceClient.ts, ConnectorGovernancePanel.tsx.
 * Depends on: nothing (leaf module — pure types).
 * Key invariants:
 *   - ConnectionMetadata is METADATA ONLY — owner_user_id + type + status + sync health. It NEVER
 *     carries username/host/port/secret/email content (that's the structural §7 enforcement). Mirror
 *     the backend ConnectionMetadataResponse field-for-field — adding a content field here would be
 *     a leak waiting to happen.
 *   - `entitled` is the Tier-1 ceiling: the UI must not offer to enable a type when entitled=false.
 */

/** A per-user grant/deny override (governance metadata). */
export interface ConnectorOverride {
  user_id: string;
  connector_type: string;
  /** "grant" (an upgrade — connect even when org-wide off) | "deny" (an exclusion). */
  override_type: "grant" | "deny";
}

/**
 * §7 metadata-only view of one connection for the admin governance roll-up. Owner + type + lifecycle
 * + sync health only — NO username/host/secret/content (those are the owner's, never the admin's).
 */
export interface ConnectionMetadata {
  id: string;
  connector_type: string;
  owner_user_id: string | null;
  status: string;
  is_enabled: boolean;
  sync_status: string;
  synced_count: number;
  total_count: number | null;
  last_synced_at: string | null;
  last_error: string | null;
}

/** The full governance view for one connector type (the panel's whole state). */
export interface ConnectorGovernance {
  connector_type: string;
  /** Tier-1 entitlement ceiling — false ⇒ the admin can't enable this type at all. */
  entitled: boolean;
  org_wide_enabled: boolean;
  overrides: ConnectorOverride[];
  connections: ConnectionMetadata[];
}
