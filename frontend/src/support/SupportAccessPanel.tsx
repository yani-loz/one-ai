/**
 * Role: Platform-side break-glass control on the org detail screen — request time-boxed
 *       access to this company (with a reason), see your requests' status, and revoke them.
 *       The customer must approve; this side can never self-approve.
 * Used by: OrganizationDetailPage (rendered inside a "Support access" Section).
 * Depends on: ./supportClient, ./types, ../platform/format, ../identity (AuthRequestError).
 * Key invariants:
 *   - Self-contained: owns its own fetch + request/revoke actions, re-rendering from the
 *     server. A 401 bubbles to a (memoized) onAuthExpired so the parent logs out.
 *   - The platform list is requester-scoped server-side; this panel further filters to THIS
 *     org so the detail screen shows only the relevant requests. Metadata only.
 */
import { useCallback, useEffect, useState } from "react";

import { AuthRequestError } from "../identity";
import { formatDateTime } from "../platform/format";
import {
  listMySupportRequests,
  requestSupportAccess,
  revokeMySupportRequest,
} from "./supportClient";
import type { SupportGrant } from "./types";

/** Human-readable status for the requesting platform admin. */
const STATUS_LABEL: Record<string, string> = {
  requested: "Awaiting customer approval",
  approved: "Approved",
  denied: "Denied by customer",
  revoked: "Revoked",
};

const REVOCABLE = new Set(["requested", "approved"]);

interface Props {
  orgId: string;
  /** Called on a 401 so the parent tears the session down. Must be stable. */
  onAuthExpired: () => void;
}

export function SupportAccessPanel({ orgId, onAuthExpired }: Props): React.JSX.Element {
  const [grants, setGrants] = useState<SupportGrant[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "loaded" | "error">("loading");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      const all = await listMySupportRequests();
      setGrants(all.filter((grant) => grant.org_id === orgId));
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

  const submitRequest = useCallback(async (): Promise<void> => {
    const trimmed = reason.trim();
    if (trimmed.length === 0) return;
    setBusy(true);
    try {
      await requestSupportAccess(orgId, trimmed);
      setReason("");
      await load();
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        onAuthExpired();
        return;
      }
      await load();
    } finally {
      setBusy(false);
    }
  }, [orgId, reason, load, onAuthExpired]);

  const revoke = useCallback(
    async (grantId: string): Promise<void> => {
      setBusy(true);
      try {
        await revokeMySupportRequest(grantId);
        await load();
      } catch (error) {
        if (error instanceof AuthRequestError && error.status === 401) {
          onAuthExpired();
          return;
        }
        await load();
      } finally {
        setBusy(false);
      }
    },
    [load, onAuthExpired],
  );

  return (
    <>
      <p className="mb-3 text-xs text-text-muted">
        Request time-boxed access to this company. The customer must approve — you can never
        self-approve, and every step is logged.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={500}
          placeholder="Reason (e.g. investigate failed sync, ticket #4821)"
          className="flex-1 rounded-lg border border-white/60 bg-white/70 px-3 py-2 text-sm text-text-primary outline-none transition focus:border-brand-teal focus:ring-2 focus:ring-brand-teal/20"
        />
        <button
          type="button"
          disabled={busy || reason.trim().length === 0}
          onClick={() => void submitRequest()}
          className="shrink-0 rounded-lg border border-brand-teal/40 bg-brand-teal/10 px-3 py-2 text-xs font-medium text-brand-teal transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Request access
        </button>
      </div>

      {loadState === "loaded" && grants.length > 0 && (
        <ul className="mt-4 space-y-2.5">
          {grants.map((grant) => (
            <li key={grant.id} className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-text-primary">
                  {grant.is_active
                    ? `Active until ${grant.expires_at !== null ? formatDateTime(grant.expires_at) : "—"}`
                    : (STATUS_LABEL[grant.status] ?? grant.status)}
                </p>
                <p className="truncate text-xs text-text-muted">
                  “{grant.reason}” · {formatDateTime(grant.created_at)}
                </p>
              </div>
              {REVOCABLE.has(grant.status) && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void revoke(grant.id)}
                  className="shrink-0 rounded-lg border border-white/50 bg-white/50 px-3 py-1.5 text-xs font-medium text-text-secondary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
