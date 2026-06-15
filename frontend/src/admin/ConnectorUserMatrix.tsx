/**
 * Role: The per-user grant/deny/inherit matrix for one connector type (CO-01 Tier 2) — the company's
 *       users × their effective access, with grant/deny/inherit controls and a §7 health hint (who
 *       has connected + sync state) drawn from the metadata-only connection roll-up. Presentation +
 *       per-user action wiring; the data + mutations come from useConnectorGovernance via props.
 * Used by: ConnectorGovernancePanel.tsx.
 * Depends on: react, ./types (CompanyUser), ./connectorGovernanceTypes, the aurora Tailwind theme.
 * Key invariants:
 *   - §7 / §8: each row shows ONLY metadata — the user's name + their effective access + whether
 *     they've connected + sync health. It NEVER renders a mailbox address, host, secret, or content
 *     (the connection roll-up is keyed by owner_user_id, carrying no such fields).
 *   - The effective state combines the per-user override (grant/deny wins) with the org-wide policy;
 *     a per-user grant is an UPGRADE (connect even when org-wide off), a deny is an exclusion (§10.1).
 */
import type { CompanyUser } from "./types";
import type { ConnectionMetadata, ConnectorOverride } from "./connectorGovernanceTypes";

type Effective = "granted" | "denied" | "inherit-on" | "inherit-off";

/** Resolve a user's effective access from their override + the org-wide policy (override wins). */
function effectiveAccess(
  override: ConnectorOverride | undefined,
  orgWideEnabled: boolean,
): Effective {
  if (override?.override_type === "grant") return "granted";
  if (override?.override_type === "deny") return "denied";
  return orgWideEnabled ? "inherit-on" : "inherit-off";
}

const ACCESS_LABEL: Record<Effective, string> = {
  granted: "Granted",
  denied: "Denied",
  "inherit-on": "Allowed (org-wide)",
  "inherit-off": "Not allowed (org-wide)",
};

const ACCESS_TONE: Record<Effective, string> = {
  granted: "text-brand-teal",
  denied: "text-brand-red",
  "inherit-on": "text-text-secondary",
  "inherit-off": "text-text-muted",
};

const CHIP_CLASS =
  "rounded-lg border border-white/50 bg-white/60 px-2.5 py-1 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.04] active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100";

/** A short §7-safe health hint for a user's connection (or "Not connected"). NO mailbox/secret. */
function healthHint(connection: ConnectionMetadata | undefined): string {
  if (connection === undefined) return "Not connected";
  if (!connection.is_enabled) return "Disabled";
  if (connection.sync_status === "running") return "Syncing…";
  if (connection.status === "error") return "Sync error";
  if (connection.synced_count > 0) {
    return `Connected · ${connection.synced_count.toLocaleString()} synced`;
  }
  return "Connected";
}

/** The dot colour for a connection's health hint (living teal pulse only when actively healthy). */
function hintDotClass(connection: ConnectionMetadata | undefined): string {
  if (connection === undefined || !connection.is_enabled) return "bg-text-muted";
  if (connection.sync_status === "running") return "bg-brand-teal animate-pulse-dot";
  if (connection.status === "error") return "bg-brand-red";
  if (connection.status === "connected") return "bg-brand-teal animate-pulse-dot";
  return "bg-text-muted";
}

/** One user's row: name + effective access + §7 health hint + grant/deny/inherit controls. */
function MatrixRow({
  user,
  override,
  connection,
  orgWideEnabled,
  busy,
  onGrant,
  onDeny,
  onClear,
}: {
  user: CompanyUser;
  override: ConnectorOverride | undefined;
  connection: ConnectionMetadata | undefined;
  orgWideEnabled: boolean;
  busy: boolean;
  onGrant: () => void;
  onDeny: () => void;
  onClear: () => void;
}): React.JSX.Element {
  const effective = effectiveAccess(override, orgWideEnabled);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/40 py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-text-primary">{user.full_name}</p>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${hintDotClass(connection)}`} />
          <span className="text-xs text-text-muted">{healthHint(connection)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className={`mr-1 text-xs font-medium ${ACCESS_TONE[effective]}`}>
          {ACCESS_LABEL[effective]}
        </span>
        <button
          type="button"
          disabled={busy || override?.override_type === "grant"}
          onClick={onGrant}
          className={`${CHIP_CLASS} hover:text-brand-teal`}
        >
          Grant
        </button>
        <button
          type="button"
          disabled={busy || override?.override_type === "deny"}
          onClick={onDeny}
          className={`${CHIP_CLASS} hover:text-brand-red`}
        >
          Deny
        </button>
        <button
          type="button"
          disabled={busy || override === undefined}
          onClick={onClear}
          className={CHIP_CLASS}
        >
          Inherit
        </button>
      </div>
    </div>
  );
}

/** The full per-user matrix for one connector type. */
export function ConnectorUserMatrix({
  users,
  overrides,
  connections,
  orgWideEnabled,
  busy,
  onGrant,
  onDeny,
  onClear,
}: {
  users: CompanyUser[];
  overrides: ConnectorOverride[];
  connections: ConnectionMetadata[];
  orgWideEnabled: boolean;
  busy: boolean;
  onGrant: (userId: string) => void;
  onDeny: (userId: string) => void;
  onClear: (userId: string) => void;
}): React.JSX.Element {
  const overrideByUser = new Map(overrides.map((override) => [override.user_id, override]));
  const connectionByOwner = new Map(
    connections
      .filter((connection) => connection.owner_user_id !== null)
      .map((connection) => [connection.owner_user_id as string, connection]),
  );

  if (users.length === 0) {
    return <p className="text-sm text-text-secondary">No users to govern yet.</p>;
  }

  return (
    <div>
      {users.map((user) => (
        <MatrixRow
          key={user.id}
          user={user}
          override={overrideByUser.get(user.id)}
          connection={connectionByOwner.get(user.id)}
          orgWideEnabled={orgWideEnabled}
          busy={busy}
          onGrant={() => onGrant(user.id)}
          onDeny={() => onDeny(user.id)}
          onClear={() => onClear(user.id)}
        />
      ))}
    </div>
  );
}
