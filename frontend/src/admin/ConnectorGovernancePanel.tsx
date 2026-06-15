/**
 * Role: The Tier-2 connector governance panel (CO-01) — a company admin's control surface for one
 *       connector type: an org-wide enable/disable toggle (locked when the company isn't entitled),
 *       a per-user grant/deny/inherit matrix, and a §7 metadata-only health roll-up (who connected +
 *       sync health). Presentation only; the governance data lives in useConnectorGovernance and the
 *       user list is reused from useCompanyUsers.
 * Used by: AdminConsolePage.tsx (surfaced as a section on the company-admin console).
 * Depends on: react, ../identity (useAuth), ./useConnectorGovernance, ./useCompanyUsers,
 *             ./ConnectorUserMatrix, the aurora Tailwind theme.
 * Key invariants:
 *   - METADATA ONLY: this panel renders the governance metadata + the §7 health roll-up — never a
 *     mailbox address, host, secret, or email content (the backend view carries none).
 *   - The org-wide toggle + every "Grant" control is DISABLED when entitled=false (the Tier-1
 *     ceiling) with a "not in your plan" hint — an admin can't enable a type beyond the plan.
 *   - The pilot governs IMAP only; the panel is single-type (the connector_type prop), so adding a
 *     second type is just another mounted panel, not a rewrite.
 */
import { useAuth } from "../identity";
import { connectorTypeLabel } from "./connectorGovernanceLabels";
import { ConnectorUserMatrix } from "./ConnectorUserMatrix";
import { useCompanyUsers } from "./useCompanyUsers";
import { useConnectorGovernance } from "./useConnectorGovernance";

/** A shimmer placeholder shown while the governance view loads (no spinners). */
function SkeletonBlock(): React.JSX.Element {
  return (
    <div className="relative h-24 overflow-hidden rounded-xl border border-white/50 bg-white/40">
      <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
    </div>
  );
}

/** The org-wide enable/disable toggle row (locked + hinted when the company isn't entitled). */
function OrgWideToggle({
  enabled,
  entitled,
  busy,
  onToggle,
}: {
  enabled: boolean;
  entitled: boolean;
  busy: boolean;
  onToggle: (next: boolean) => void;
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary">
          {enabled ? "Enabled org-wide" : "Disabled org-wide"}
        </p>
        <p className="text-xs text-text-muted">
          {entitled
            ? "When on, everyone may connect this source (unless individually denied)."
            : "Not included in your company's plan — ask your One AI contact to enable it."}
        </p>
      </div>
      <button
        type="button"
        disabled={busy || !entitled}
        onClick={() => onToggle(!enabled)}
        aria-pressed={enabled}
        className={`shrink-0 rounded-lg border px-3 py-2 text-xs font-medium transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 ${
          enabled
            ? "border-brand-teal/40 bg-brand-teal/10 text-brand-teal"
            : "border-white/50 bg-white/50 text-text-primary"
        }`}
      >
        {enabled ? "Disable org-wide" : "Enable org-wide"}
      </button>
    </div>
  );
}

/**
 * Render the governance panel for one connector type.
 *
 * Contract: `connectorType` selects which type to govern (the pilot passes "imap"). Renders a glass
 * section with the org-wide toggle, the per-user matrix, and the metadata-only health roll-up.
 */
export function ConnectorGovernancePanel({
  connectorType = "imap",
}: {
  connectorType?: string;
}): React.JSX.Element {
  const { user, logout } = useAuth();
  const governance = useConnectorGovernance(connectorType, logout);
  const companyUsers = useCompanyUsers(user?.id ?? "", logout);

  const label = connectorTypeLabel(connectorType);

  return (
    <section className="animate-fade-in rounded-xl border border-white/50 bg-white/65 p-5 shadow-sm backdrop-blur-xl">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          Connector access · {label}
        </h2>
        {governance.governance !== null && !governance.governance.entitled && (
          <span className="rounded-full border border-text-muted/30 bg-white/50 px-2 py-0.5 text-xs text-text-muted">
            Not in plan
          </span>
        )}
      </div>

      {governance.notice !== null && (
        <p
          role="alert"
          className="animate-fade-in mb-3 rounded-lg border border-brand-red/30 bg-brand-red/10 px-3 py-2 text-sm text-brand-red"
        >
          {governance.notice}
        </p>
      )}

      {governance.loadState === "loading" && <SkeletonBlock />}

      {governance.loadState === "error" && (
        <div className="rounded-xl border border-brand-red/30 bg-brand-red/10 p-4 text-center">
          <p className="text-sm text-brand-red">Couldn&apos;t load connector governance.</p>
          <button
            type="button"
            onClick={() => void governance.reload()}
            className="mt-3 rounded-lg border border-white/50 bg-white/60 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
          >
            Retry
          </button>
        </div>
      )}

      {governance.loadState === "loaded" && governance.governance !== null && (
        <>
          <OrgWideToggle
            enabled={governance.governance.org_wide_enabled}
            entitled={governance.governance.entitled}
            busy={governance.busy}
            onToggle={(next) => void governance.toggleOrgWide(next)}
          />

          <div className="mt-5">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
              Per-user access &amp; health
            </h3>
            <ConnectorUserMatrix
              users={companyUsers.users}
              overrides={governance.governance.overrides ?? []}
              connections={governance.governance.connections ?? []}
              orgWideEnabled={governance.governance.org_wide_enabled}
              busy={governance.busy || !governance.governance.entitled}
              onGrant={(userId) => void governance.grant(userId)}
              onDeny={(userId) => void governance.deny(userId)}
              onClear={(userId) => void governance.clearOverride(userId)}
            />
          </div>
        </>
      )}
    </section>
  );
}
