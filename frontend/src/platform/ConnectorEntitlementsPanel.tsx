/**
 * Role: The Tier-1 connector ENTITLEMENT panel (CO-01) on the org-detail screen — a platform admin
 *       grants/revokes each connector type for one company (the plan ceiling). Sibling of the
 *       SealedBanner / SupportAccessPanel / OrgErasurePanel sections; matches their style.
 * Used by: OrganizationDetailPage.tsx (the "Connector entitlements" section).
 * Depends on: react, ../identity (AuthRequestError), ./connectorEntitlementClient, the aurora theme.
 * Key invariants:
 *   - Acts on the {orgId} PATH (a platform admin acts across orgs), never a JWT-derived org. A 401
 *     calls `onAuthExpired` (logout); other failures reload the authoritative entitlement list.
 *   - Metadata only — entitlement carries no credential/content. The known connector types are
 *     enumerated client-side (the pilot = IMAP/Email); a missing entitlement row reads as "not
 *     granted" (revoking persists policies/connections, re-exposed on re-grant — no cascade).
 */
import { useCallback, useEffect, useState } from "react";

import { AuthRequestError } from "../identity";
import {
  listConnectorEntitlements,
  setConnectorEntitlement,
  type ConnectorEntitlement,
} from "./connectorEntitlementClient";

type LoadState = "loading" | "loaded" | "error";

/** The connector types the platform console can grant (the pilot = IMAP/Email). */
const KNOWN_TYPES: { type: string; label: string }[] = [{ type: "imap", label: "Email (IMAP)" }];

/** Whether a company is currently entitled to a type (a missing row reads as not granted). */
function isEnabled(entitlements: ConnectorEntitlement[], type: string): boolean {
  const row = entitlements.find((entitlement) => entitlement.connector_type === type);
  return row?.enabled ?? false;
}

/** One connector type's grant/revoke row. */
function EntitlementRow({
  label,
  enabled,
  busy,
  onToggle,
}: {
  label: string;
  enabled: boolean;
  busy: boolean;
  onToggle: () => void;
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/40 py-3 last:border-b-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary">{label}</p>
        <p className="text-xs text-text-muted">
          {enabled ? "Included in this company's plan." : "Not included in this company's plan."}
        </p>
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={onToggle}
        aria-pressed={enabled}
        className={`shrink-0 rounded-lg border px-3 py-2 text-xs font-medium transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 ${
          enabled
            ? "border-brand-teal/40 bg-brand-teal/10 text-brand-teal"
            : "border-white/50 bg-white/50 text-text-primary"
        }`}
      >
        {enabled ? "Revoke" : "Grant"}
      </button>
    </div>
  );
}

/**
 * Render the connector-entitlement toggles for one company.
 *
 * Contract: `orgId` is the target company; `onAuthExpired` is called on a 401 so the parent logs
 * out. Loads the entitlement list on mount; each toggle PUTs and re-renders from the server's row.
 */
export function ConnectorEntitlementsPanel({
  orgId,
  onAuthExpired,
}: {
  orgId: string;
  onAuthExpired: () => void;
}): React.JSX.Element {
  const [entitlements, setEntitlements] = useState<ConnectorEntitlement[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busyType, setBusyType] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoadState("loading");
    try {
      setEntitlements(await listConnectorEntitlements(orgId));
      setLoadState("loaded");
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        onAuthExpired();
        return;
      }
      setLoadState("error");
    }
  }, [orgId, onAuthExpired]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = useCallback(
    async (type: string, nextEnabled: boolean): Promise<void> => {
      setBusyType(type);
      try {
        const updated = await setConnectorEntitlement(orgId, type, nextEnabled);
        setEntitlements((current) => {
          const others = current.filter((entitlement) => entitlement.connector_type !== type);
          return [...others, updated];
        });
      } catch (error) {
        if (error instanceof AuthRequestError && error.status === 401) {
          onAuthExpired();
          return;
        }
        await load(); // reload the authoritative state on any other failure
      } finally {
        setBusyType(null);
      }
    },
    [orgId, onAuthExpired, load],
  );

  if (loadState === "loading") {
    return (
      <div className="relative h-16 overflow-hidden rounded-xl border border-white/50 bg-white/40">
        <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
      </div>
    );
  }

  if (loadState === "error") {
    return (
      <div className="rounded-xl border border-brand-red/30 bg-brand-red/10 p-4 text-center">
        <p className="text-sm text-brand-red">Couldn&apos;t load connector entitlements.</p>
        <button
          type="button"
          onClick={() => void load()}
          className="mt-3 rounded-lg border border-white/50 bg-white/60 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      <p className="mb-3 text-xs text-text-muted">
        Grant the connector types this company&apos;s plan includes. Revoking hides a type from the
        company; its existing config is kept and re-exposed if you grant it again.
      </p>
      {KNOWN_TYPES.map((known) => {
        const enabled = isEnabled(entitlements, known.type);
        return (
          <EntitlementRow
            key={known.type}
            label={known.label}
            enabled={enabled}
            busy={busyType === known.type}
            onToggle={() => void toggle(known.type, !enabled)}
          />
        );
      })}
    </div>
  );
}
