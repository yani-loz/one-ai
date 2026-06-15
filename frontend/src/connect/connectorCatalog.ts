/**
 * Role: Pure presentation catalog for connector TYPES — maps a backend connector_type string to its
 *       human label, a short description, and a glyph for the panel cards + detail header. The single
 *       place the UI knows "imap" is "Email".
 * Used by: ConnectorCard, MyConnectionsPage, ConnectorDetailPage (label/icon/description).
 * Depends on: nothing (pure, leaf — directly unit-tested).
 * Key invariants:
 *   - For the pilot the only real type is `imap` (Email). An unknown type falls back to a neutral
 *     label/glyph so a future backend-added type never renders blank — forward-compatible by design.
 */

/** The display metadata for one connector type. */
export interface ConnectorTypeMeta {
  /** The wire value (matches the backend ConnectorType enum). */
  type: string;
  /** Human label shown on the card + detail header. */
  label: string;
  /** One-line description shown under the label. */
  description: string;
  /** A simple emoji glyph (no icon dependency); the design language carries the colour/animation. */
  glyph: string;
}

const CATALOG: Record<string, ConnectorTypeMeta> = {
  imap: {
    type: "imap",
    label: "Email",
    description: "Connect your mailbox so One AI can learn from your email.",
    glyph: "✉️",
  },
};

/**
 * Look up the display metadata for a connector type.
 *
 * Contract: returns the catalog entry for a known type, else a neutral fallback built from the raw
 * type string (never null) so an unrecognised future type still renders a usable card.
 */
export function connectorTypeMeta(type: string): ConnectorTypeMeta {
  return (
    CATALOG[type] ?? {
      type,
      label: type,
      description: "Connect this source so One AI can learn from it.",
      glyph: "🔌",
    }
  );
}
