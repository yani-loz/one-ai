/**
 * Role: Stateful controller for the company-admin user list — owns the fetch, the seat
 *       stats, and every mutation (role change, deactivate behind a confirm, reactivate,
 *       self-demotion). Keeps AdminConsolePage thin (presentation only).
 * Used by: AdminConsolePage.tsx.
 * Depends on: react (hooks), ./adminClient, ./types, ../identity (AuthRequestError, via statusOf).
 * Key invariants:
 *   - A 401 (no/expired session) OR 403 (role downgraded mid-session — a company_admin never
 *     legitimately gets 403 from /users) invokes `logout` and stops; the route guard redirects.
 *   - Self-targeting actions (demote/deactivate your own account) go through the confirm
 *     dialog and, on success, call `logout` so the admin re-authenticates with their new
 *     (reduced) privileges rather than sitting in a stale-role limbo.
 *   - The last-admin guard is server-side (409); this surfaces it as a friendly message,
 *     in the page notice (inline changes) or the confirm dialog (confirmed actions).
 *   - Post-mutation refetches are SILENT (keep the table mounted, no skeleton flash) and
 *     sequence-guarded so an out-of-order reload can't apply a stale list.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AuthRequestError } from "../identity";
import { deactivateCompanyUser, listCompanyUsers, updateCompanyUser } from "./adminClient";
import type { CompanyUser, CompanyUserRole } from "./types";

export type LoadState = "loading" | "loaded" | "error";
type ConfirmKind = "deactivate" | "demote-self";
interface PendingConfirm {
  kind: ConfirmKind;
  user: CompanyUser;
}

/** Title + body + button label for the confirm dialog, by action kind. */
export interface ConfirmCopy {
  title: string;
  message: string;
  confirmLabel: string;
}

/** Everything AdminConsolePage needs to render + drive the user list. */
export interface CompanyUsersController {
  users: CompanyUser[];
  loadState: LoadState;
  busyUserId: string | null;
  notice: string | null;
  stats: { total: number; active: number; admins: number };
  confirmOpen: boolean;
  confirmCopy: ConfirmCopy | null;
  confirmBusy: boolean;
  confirmError: string | null;
  reload: (silent?: boolean) => Promise<void>;
  changeRole: (target: CompanyUser, role: CompanyUserRole) => void;
  reactivate: (target: CompanyUser) => void;
  requestDeactivate: (target: CompanyUser) => void;
  confirmPending: () => Promise<void>;
  cancelConfirm: () => void;
}

const LAST_ADMIN_MESSAGE = "The organisation must keep at least one administrator.";
const GENERIC_MESSAGE = "Something went wrong. Please try again.";

/** HTTP status carried by an AuthRequestError, or 0 for a non-HTTP failure. */
function statusOf(error: unknown): number {
  return error instanceof AuthRequestError ? error.status : 0;
}

/** 401 (no/expired session) and 403 (role no longer permits this) both mean: re-authenticate.
 *  A legitimate company_admin never gets a 403 from /users (require_company_admin), so a 403
 *  here means the caller's role was downgraded mid-session — log out and let the guard sort it. */
function isAuthFailure(status: number): boolean {
  return status === 401 || status === 403;
}

function describeConfirm(pending: PendingConfirm, isSelf: boolean): ConfirmCopy {
  if (pending.kind === "demote-self") {
    return {
      title: "Remove your own admin access?",
      message:
        "You'll become a member and be signed out. Another administrator has to restore your access.",
      confirmLabel: "Make me a member",
    };
  }
  if (isSelf) {
    return {
      title: "Deactivate your own account?",
      message:
        "You'll be signed out and won't be able to sign back in until another administrator reactivates you.",
      confirmLabel: "Deactivate",
    };
  }
  return {
    title: `Deactivate ${pending.user.full_name}?`,
    message:
      "They lose access immediately. You can reactivate them later. The organisation's last administrator can't be deactivated.",
    confirmLabel: "Deactivate",
  };
}

/**
 * Manage the caller's organization users.
 *
 * Contract: loads the list on mount; exposes seat stats, the busy/notice/confirm state, and
 * mutation actions. `currentUserId` flags self-targeting actions; `logout` is called on a 401
 * or after a successful self-targeting change.
 */
export function useCompanyUsers(
  currentUserId: string,
  logout: () => Promise<void>,
): CompanyUsersController {
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  // Monotonic id so an out-of-order refetch (two mutations racing their reloads) cannot apply a
  // stale list: only the most-recently-issued reload commits its result.
  const reloadSeq = useRef(0);

  const reload = useCallback(
    async (silent = false): Promise<void> => {
      const seq = ++reloadSeq.current;
      // A silent reload (after a mutation) keeps the table mounted instead of flashing the whole
      // list to skeletons; only the initial load and an explicit Retry use the loud state.
      if (!silent) setLoadState("loading");
      try {
        const list = await listCompanyUsers();
        if (seq !== reloadSeq.current) return; // superseded by a newer reload
        setUsers(list);
        setLoadState("loaded");
      } catch (error) {
        if (seq !== reloadSeq.current) return;
        if (isAuthFailure(statusOf(error))) {
          await logout();
          return;
        }
        setLoadState("error");
      }
    },
    [logout],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  const stats = useMemo(() => {
    const active = users.filter((candidate) => candidate.is_active);
    const admins = active.filter((candidate) => candidate.role === "company_admin");
    return { total: users.length, active: active.length, admins: admins.length };
  }, [users]);

  const applyInlineUpdate = useCallback(
    async (
      userId: string,
      payload: { role?: CompanyUserRole; is_active?: boolean },
      failCopy: string,
    ): Promise<void> => {
      setNotice(null);
      setBusyUserId(userId);
      try {
        await updateCompanyUser(userId, payload);
        await reload(true);
      } catch (error) {
        if (isAuthFailure(statusOf(error))) {
          await logout();
          return;
        }
        setNotice(statusOf(error) === 409 ? failCopy : GENERIC_MESSAGE);
      } finally {
        setBusyUserId(null);
      }
    },
    [reload, logout],
  );

  const changeRole = useCallback(
    (target: CompanyUser, role: CompanyUserRole): void => {
      if (role === target.role) return;
      // Demoting yourself out of admin is a self-eject — confirm it, then log out on success.
      if (target.id === currentUserId && role === "member") {
        setNotice(null);
        setConfirmError(null);
        setPending({ kind: "demote-self", user: target });
        return;
      }
      void applyInlineUpdate(target.id, { role }, LAST_ADMIN_MESSAGE);
    },
    [applyInlineUpdate, currentUserId],
  );

  const reactivate = useCallback(
    (target: CompanyUser): void => {
      void applyInlineUpdate(target.id, { is_active: true }, "Couldn't reactivate the user.");
    },
    [applyInlineUpdate],
  );

  const requestDeactivate = useCallback((target: CompanyUser): void => {
    setNotice(null);
    setConfirmError(null);
    setPending({ kind: "deactivate", user: target });
  }, []);

  const confirmPending = useCallback(async (): Promise<void> => {
    if (pending === null) return;
    setConfirmBusy(true);
    setConfirmError(null);
    const isSelf = pending.user.id === currentUserId;
    try {
      if (pending.kind === "deactivate") {
        await deactivateCompanyUser(pending.user.id);
      } else {
        await updateCompanyUser(pending.user.id, { role: "member" });
      }
      // A self-targeting change reduces your own privileges — re-authenticate cleanly.
      if (isSelf) {
        await logout();
        return;
      }
      setPending(null);
      await reload(true);
    } catch (error) {
      if (isAuthFailure(statusOf(error))) {
        await logout();
        return;
      }
      setConfirmError(statusOf(error) === 409 ? LAST_ADMIN_MESSAGE : GENERIC_MESSAGE);
    } finally {
      setConfirmBusy(false);
    }
  }, [pending, currentUserId, logout, reload]);

  const cancelConfirm = useCallback((): void => {
    setPending(null);
    setConfirmError(null);
  }, []);

  const confirmCopy =
    pending !== null ? describeConfirm(pending, pending.user.id === currentUserId) : null;

  return {
    users,
    loadState,
    busyUserId,
    notice,
    stats,
    confirmOpen: pending !== null,
    confirmCopy,
    confirmBusy,
    confirmError,
    reload,
    changeRole,
    reactivate,
    requestDeactivate,
    confirmPending,
    cancelConfirm,
  };
}
