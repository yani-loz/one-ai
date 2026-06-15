/**
 * Role: Pure label lookup for connector TYPES in the admin governance UI — maps a backend
 *       connector_type string to a human label for the panel heading. Kept separate from the
 *       Tier-3 connect catalog so the admin module owns its own tiny presentation map.
 * Used by: ConnectorGovernancePanel.tsx.
 * Depends on: nothing (pure, leaf).
 * Key invariants:
 *   - The pilot governs `imap` (Email) only; an unknown type falls back to its raw string so a
 *     future backend-added type never renders blank.
 */

const LABELS: Record<string, string> = {
  imap: "Email",
};

/** Human label for a connector type (falls back to the raw type string for unknown types). */
export function connectorTypeLabel(type: string): string {
  return LABELS[type] ?? type;
}
