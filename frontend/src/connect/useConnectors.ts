/**
 * Role: Stateful controller for the connectors list — owns the fetch and every per-row lifecycle
 *       action (test, enable/disable, delete). Keeps ConnectorsPage thin (presentation only).
 * Used by: ConnectorsPage.tsx.
 * Depends on: react (hooks), ./connectClient, ./types, ../identity (AuthRequestError via statusOf).
 * Key invariants:
 *   - A 401 (lapsed session) OR 403 (role downgraded mid-session — a company_admin never
 *     legitimately gets 403 from /connectors) calls `logout` and stops; the route guard redirects.
 *   - Post-action reloads are SILENT (keep the table mounted, no skeleton flash) and
 *     sequence-guarded so an out-of-order refetch can't apply a stale list.
 *   - Actions are mutually exclusive per row via `busyId`; a failure surfaces a friendly notice.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { AuthRequestError } from "../identity";
import {
  deleteConnection,
  disableConnection,
  enableConnection,
  listConnections,
  startSync,
  testConnection,
} from "./connectClient";
import type { Connection } from "./types";

/** How often the list is silently refreshed while any connection is actively syncing. */
const SYNC_POLL_MS = 2500;

export type LoadState = "loading" | "loaded" | "error";

/** Everything ConnectorsPage needs to render + drive the connection list. */
export interface ConnectorsController {
  connections: Connection[];
  loadState: LoadState;
  busyId: string | null;
  notice: string | null;
  reload: (silent?: boolean) => Promise<void>;
  test: (connection: Connection) => Promise<void>;
  sync: (connection: Connection) => Promise<void>;
  setEnabled: (connection: Connection, enabled: boolean) => Promise<void>;
  remove: (connection: Connection) => Promise<void>;
}

const GENERIC_MESSAGE = "Something went wrong. Please try again.";

function statusOf(error: unknown): number {
  return error instanceof AuthRequestError ? error.status : 0;
}

/** 401/403 both mean re-authenticate (a company_admin never legitimately gets 403 here). */
function isAuthFailure(status: number): boolean {
  return status === 401 || status === 403;
}

/**
 * Manage the caller's org connector connections.
 *
 * Contract: loads on mount; exposes the list, load/busy/notice state, and the lifecycle actions.
 * `logout` is called on a 401/403 so the parent redirects to /login.
 */
export function useConnectors(logout: () => Promise<void>): ConnectorsController {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reloadSeq = useRef(0);

  const reload = useCallback(
    async (silent = false): Promise<void> => {
      const seq = ++reloadSeq.current;
      if (!silent) setLoadState("loading");
      try {
        const list = await listConnections();
        if (seq !== reloadSeq.current) return; // superseded by a newer reload
        setConnections(list);
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

  // While any connection is actively syncing, silently refresh the list so the N/M progress
  // advances live; the poll stops the moment none are running (idle/error) — no idle churn.
  const anySyncing = connections.some((connection) => connection.sync_status === "running");
  useEffect(() => {
    if (!anySyncing) return;
    const timer = window.setInterval(() => void reload(true), SYNC_POLL_MS);
    return () => window.clearInterval(timer);
  }, [anySyncing, reload]);

  /** Run a per-row mutation, then silently reload; auth failure → logout, else a friendly notice. */
  const runAction = useCallback(
    async (connectionId: string, action: () => Promise<unknown>): Promise<void> => {
      setNotice(null);
      setBusyId(connectionId);
      try {
        await action();
        await reload(true);
      } catch (error) {
        if (isAuthFailure(statusOf(error))) {
          await logout();
          return;
        }
        setNotice(GENERIC_MESSAGE);
      } finally {
        setBusyId(null);
      }
    },
    [reload, logout],
  );

  const test = useCallback(
    (connection: Connection) => runAction(connection.id, () => testConnection(connection.id)),
    [runAction],
  );

  /** Trigger a sync; a 409 (already running / disabled) is a no-op — the reload reflects reality. */
  const sync = useCallback(
    (connection: Connection) =>
      runAction(connection.id, async () => {
        try {
          await startSync(connection.id);
        } catch (error) {
          if (statusOf(error) === 409) return;
          throw error;
        }
      }),
    [runAction],
  );

  const setEnabled = useCallback(
    (connection: Connection, enabled: boolean) =>
      runAction(connection.id, () =>
        enabled ? enableConnection(connection.id) : disableConnection(connection.id),
      ),
    [runAction],
  );

  const remove = useCallback(
    (connection: Connection) => runAction(connection.id, () => deleteConnection(connection.id)),
    [runAction],
  );

  return { connections, loadState, busyId, notice, reload, test, sync, setEnabled, remove };
}
