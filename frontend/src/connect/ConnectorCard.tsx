/**
 * Role: One connector-TYPE card in the "My Connections" panel — glyph + label + a one-line health
 *       summary (connected? / syncing… / records synced / last-sync), linking to that type's detail.
 *       An allowed-but-not-yet-connected type invites a connect; a not-allowed type renders locked
 *       with its admin reason (never a dead link).
 * Used by: MyConnectionsPage.tsx (the card grid).
 * Depends on: react-router-dom (Link), ./connectorCatalog, ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - `connection` is the caller's OWN connection of this type (or null = not connected). Disabled
 *     (admin-revoked) wins over health in the summary, matching ConnectorStatusBadge's precedence.
 *   - A not-allowed card is non-interactive (no Link), shows the lock + the friendly reason; an
 *     allowed card always routes to /connections/:type.
 */
import { Link } from "react-router-dom";

import { connectorTypeMeta } from "./connectorCatalog";
import type { AllowedConnectorType, Connection } from "./types";

/** A short health line for the card: syncing progress, records synced, last sync, or "Not connected". */
function summaryFor(connection: Connection | null): string {
  if (connection === null) return "Not connected";
  if (!connection.is_enabled) return "Disabled by your administrator";
  if (connection.sync_status === "running") {
    const total = connection.total_count;
    const progress =
      total !== null ? `${connection.synced_count} / ${total}` : `${connection.synced_count}`;
    return `Syncing… ${progress} messages`;
  }
  if (connection.synced_count > 0) {
    return `${connection.synced_count.toLocaleString()} messages synced`;
  }
  if (connection.status === "error") return "Connection error — needs attention";
  if (connection.status === "connected") return "Connected — ready to sync";
  return "Connected — not yet verified";
}

/** The small status dot reflecting the card's combined enabled + health + sync state. */
function dotClassFor(connection: Connection | null): string {
  if (connection === null || !connection.is_enabled) return "bg-text-muted";
  if (connection.sync_status === "running") return "bg-brand-teal animate-pulse-dot";
  if (connection.status === "error") return "bg-brand-red";
  if (connection.status === "connected") return "bg-brand-teal animate-pulse-dot";
  return "bg-text-muted";
}

/** Render one connector-type card (allowed → link to detail; not-allowed → locked with the reason). */
export function ConnectorCard({
  allowed,
  connection,
}: {
  allowed: AllowedConnectorType;
  connection: Connection | null;
}): React.JSX.Element {
  const meta = connectorTypeMeta(allowed.connector_type);

  if (!allowed.allowed) {
    return (
      <div className="animate-fade-in rounded-xl border border-white/50 bg-white/40 p-5 opacity-70">
        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="text-2xl grayscale">
            {meta.glyph}
          </span>
          <div className="min-w-0">
            <p className="font-semibold text-text-secondary">{meta.label}</p>
            <p className="truncate text-xs text-text-muted">🔒 {allowed.reason ?? "Not available"}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Link
      to={`/connections/${allowed.connector_type}`}
      className="group animate-fade-in block rounded-xl border border-white/50 bg-white/65 p-5 shadow-sm backdrop-blur-xl transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.99]"
    >
      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="text-2xl">
          {meta.glyph}
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-text-primary">{meta.label}</p>
          <p className="truncate text-xs text-text-muted">{meta.description}</p>
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2 text-sm font-medium text-text-secondary">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dotClassFor(connection)}`} />
        <span>{summaryFor(connection)}</span>
      </div>
    </Link>
  );
}
