/**
 * Role: The append-only audit trail for one company on the detail screen — a newest-first
 *       record of who did what (suspend, legal hold, sign-ins, onboarding). Metadata only:
 *       it shows actions, never tenant content or secrets.
 * Used by: OrganizationDetailPage.tsx (rendered inside a "Audit trail" Section once loaded).
 * Depends on: ./platformClient (getOrganizationAudit), ./format (formatDateTime),
 *             ../identity (AuthRequestError), ./types.
 * Key invariants:
 *   - Self-contained: owns its own fetch keyed on (orgId, reloadSignal), so a lifecycle
 *     action on the parent bumps reloadSignal and the trail re-fetches — a suspend shows up
 *     on reload (PC-04-AC8). onAuthExpired MUST be stable (memoized) to avoid a refetch loop.
 *   - A 401 bubbles to onAuthExpired (the parent logs out); nothing tenant-scoped is shown.
 */
import { useCallback, useEffect, useState } from "react";

import { AuthRequestError } from "../identity";
import { formatDateTime } from "./format";
import { getOrganizationAudit } from "./platformClient";
import type { AuditLogEntry } from "./types";

/** Page size requested; if a full page comes back we hint that older entries exist. */
const TRAIL_LIMIT = 25;

/** Human-readable labels for the emitted action namespace (unknown → the raw action). */
const ACTION_LABELS: Record<string, string> = {
  "auth.login.success": "Signed in",
  "auth.login.failure": "Failed sign-in",
  "auth.login.blocked": "Blocked sign-in (suspended)",
  "auth.refresh": "Session refreshed",
  "auth.logout": "Signed out",
  "org.onboard": "Company onboarded",
  "org.suspend": "Access suspended",
  "org.reactivate": "Access reactivated",
  "org.status_change": "Status changed",
  "org.legal_hold.set": "Legal hold placed",
  "org.legal_hold.clear": "Legal hold cleared",
};

const FAILURE_ACTIONS = new Set(["auth.login.failure", "auth.login.blocked"]);

type LoadState = "loading" | "loaded" | "error";

interface AuditTrailProps {
  orgId: string;
  /** Bumped by the parent after a lifecycle mutation to force a re-fetch (AC8). */
  reloadSignal: number;
  /** Called on a 401 so the parent can tear down the lapsed session. Must be stable. */
  onAuthExpired: () => void;
}

function labelFor(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** Who acted — the denormalized email, or a role/system fallback. */
function actorFor(entry: AuditLogEntry): string {
  if (entry.actor_email !== null && entry.actor_email !== "") return entry.actor_email;
  if (entry.actor_type === "platform_admin") return "Platform admin";
  if (entry.actor_type === "system") return "System";
  return "—";
}

/** Dot colour by action category: red = failed/blocked, purple = admin org action, teal = auth. */
function dotClass(action: string): string {
  if (FAILURE_ACTIONS.has(action)) return "bg-brand-red";
  if (action.startsWith("org.")) return "bg-brand-purple";
  return "bg-brand-teal";
}

export function AuditTrail({
  orgId,
  reloadSignal,
  onAuthExpired,
}: AuditTrailProps): React.JSX.Element {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  const load = useCallback(async (): Promise<void> => {
    setLoadState("loading");
    try {
      setEntries(await getOrganizationAudit(orgId, TRAIL_LIMIT));
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
  }, [load, reloadSignal]);

  if (loadState === "loading") {
    return (
      <div className="space-y-2" aria-label="Loading audit trail">
        {[0, 1, 2].map((row) => (
          <div key={row} className="relative h-6 overflow-hidden rounded-md bg-white/40">
            <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/60 to-transparent" />
          </div>
        ))}
      </div>
    );
  }

  if (loadState === "error") {
    return (
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-brand-red">Couldn&apos;t load the audit trail.</p>
        <button
          type="button"
          onClick={() => void load()}
          className="shrink-0 rounded-lg border border-white/50 bg-white/50 px-3 py-1.5 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97]"
        >
          Retry
        </button>
      </div>
    );
  }

  if (entries === null || entries.length === 0) {
    return <p className="text-xs text-text-muted">No recorded activity yet.</p>;
  }

  return (
    <>
      <ul className="space-y-2.5">
        {entries.map((entry) => (
          <li key={entry.id} className="flex items-start gap-3 animate-fade-in">
            <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dotClass(entry.action)}`} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-text-primary">{labelFor(entry.action)}</p>
              <p className="truncate text-xs text-text-muted">
                {actorFor(entry)} · {formatDateTime(entry.occurred_at)}
              </p>
            </div>
          </li>
        ))}
      </ul>
      {entries.length === TRAIL_LIMIT && (
        <p className="mt-3 text-xs text-text-muted">Showing the {TRAIL_LIMIT} most recent.</p>
      )}
    </>
  );
}
