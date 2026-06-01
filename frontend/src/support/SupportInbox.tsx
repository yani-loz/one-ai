/**
 * Role: Company-side break-glass approval inbox (Human-in-the-Loop). A company_admin sees
 *       support-access requests targeting THEIR org and approves / denies / revokes them.
 *       Pending requests glow (animate-clari-pulse) — "this needs your approval".
 * Used by: HomePage (rendered for a company_admin only).
 * Depends on: ./supportClient, ./types, ../platform/format.
 * Key invariants:
 *   - Pull-only: fetches on mount + after each decision (no push notification yet).
 *   - Metadata only — shows WHO is asking + WHY, never tenant content.
 *   - Renders nothing unless there is something actionable (pending or an active grant), so
 *     the home stays clean. SELF-EFFACING: any fetch error (incl. 401) just hides the widget
 *     — it must never tear down the session, since it's a secondary panel on an already-
 *     authenticated home.
 */
import { useCallback, useEffect, useState } from "react";

import { formatDateTime } from "../platform/format";
import {
  approveSupportRequest,
  denySupportRequest,
  listSupportInbox,
  revokeSupportRequest,
} from "./supportClient";
import type { SupportGrant } from "./types";

type LoadState = "loading" | "loaded" | "error";
type GrantAction = (grantId: string) => Promise<SupportGrant>;

/** Pending (awaiting decision) or currently-active grants are the only actionable ones. */
function isActionable(grant: SupportGrant): boolean {
  return grant.status === "requested" || grant.is_active;
}

export function SupportInbox(): React.JSX.Element | null {
  const [grants, setGrants] = useState<SupportGrant[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      setGrants(await listSupportInbox());
      setLoadState("loaded");
    } catch {
      setLoadState("error"); // self-effacing: hide the widget, never log out
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (grantId: string, action: GrantAction): Promise<void> => {
      setBusyId(grantId);
      try {
        await action(grantId);
      } catch {
        // swallow — the reload below re-renders from the server's authoritative state
      } finally {
        await load();
        setBusyId(null);
      }
    },
    [load],
  );

  const actionable = grants.filter(isActionable);
  // Stay invisible until there's something to act on — keep the home uncluttered.
  if (loadState !== "loaded" || actionable.length === 0) {
    return null;
  }

  return (
    <section className="animate-fade-in mt-6 w-full rounded-xl border border-white/50 bg-white/65 p-5 text-left shadow-sm backdrop-blur-xl">
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        Support access
      </h2>
      <p className="mb-3 text-xs text-text-muted">
        One AI staff requesting time-boxed access to your workspace. Nothing is shared unless
        you approve.
      </p>
      <ul className="space-y-3">
        {actionable.map((grant) => (
          <SupportInboxItem
            key={grant.id}
            grant={grant}
            busy={busyId === grant.id}
            onApprove={() => void act(grant.id, approveSupportRequest)}
            onDeny={() => void act(grant.id, denySupportRequest)}
            onRevoke={() => void act(grant.id, revokeSupportRequest)}
          />
        ))}
      </ul>
    </section>
  );
}

interface ItemProps {
  grant: SupportGrant;
  busy: boolean;
  onApprove: () => void;
  onDeny: () => void;
  onRevoke: () => void;
}

/** One inbox row — a pending request (approve/deny, glowing) or an active grant (revoke). */
function SupportInboxItem({
  grant,
  busy,
  onApprove,
  onDeny,
  onRevoke,
}: ItemProps): React.JSX.Element {
  const requester = grant.requested_by_email ?? "One AI support";
  if (grant.status === "requested") {
    return (
      <li className="animate-clari-pulse rounded-lg border border-brand-purple/30 bg-white/50 p-3">
        <p className="text-sm font-medium text-text-primary">
          {requester} requests access
        </p>
        <p className="mt-0.5 text-xs text-text-secondary">“{grant.reason}”</p>
        <p className="mt-0.5 text-xs text-text-muted">
          {formatDateTime(grant.created_at)}
        </p>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onApprove}
            className="rounded-lg border border-brand-teal/40 bg-brand-teal/10 px-3 py-1.5 text-xs font-medium text-brand-teal transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDeny}
            className="rounded-lg border border-white/50 bg-white/50 px-3 py-1.5 text-xs font-medium text-text-secondary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
          >
            Deny
          </button>
        </div>
      </li>
    );
  }
  // Active (approved + within the time box).
  return (
    <li className="rounded-lg border border-white/50 bg-white/40 p-3">
      <p className="text-sm font-medium text-text-primary">{requester} has access</p>
      <p className="mt-0.5 text-xs text-text-muted">
        Active until {grant.expires_at !== null ? formatDateTime(grant.expires_at) : "—"}
      </p>
      <button
        type="button"
        disabled={busy}
        onClick={onRevoke}
        className="mt-2.5 rounded-lg border border-brand-red/40 bg-brand-red/10 px-3 py-1.5 text-xs font-medium text-brand-red transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
      >
        Revoke access
      </button>
    </li>
  );
}
