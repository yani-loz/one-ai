/**
 * Role: Stateful controller for the Tier-3 "My Connections" plane — owns the fetch of the allowed
 *       connector types + the caller's own connections, and every per-connection action (self-
 *       connect, test, sync, disconnect+erase). Keeps the panel/detail components thin.
 * Used by: MyConnectionsPage.tsx (panel) and ConnectorDetailPage.tsx (per-type detail).
 * Depends on: react (hooks), ./meConnectorClient, ./types, ../identity (AuthRequestError via statusOf).
 * Key invariants:
 *   - A 401 (lapsed session) calls `logout` and stops; the route guard redirects. Unlike the admin
 *     plane, a 403 is NOT an auth failure here — it is a legitimate "your administrator hasn't
 *     enabled this" denial surfaced from the allowed-types list, never a forced logout.
 *   - Post-action reloads are SILENT (keep the list mounted, no skeleton flash) and sequence-guarded
 *     so an out-of-order refetch can't apply a stale list.
 *   - While any connection is actively syncing the list is silently polled so the N/M advances live;
 *     the poll stops the moment none are running (no idle churn).
 *   - Actions are mutually exclusive per connection via `busyId`; a failure surfaces a friendly notice.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { AuthRequestError } from "../identity";
import {
  disconnectMyConnection,
  listAllowedTypes,
  listMyConnections,
  startMySync,
  testMyConnection,
} from "./meConnectorClient";
import type { AllowedConnectorType, Connection } from "./types";

/** How often the list is silently refreshed while any connection is actively syncing. */
const SYNC_POLL_MS = 2500;

export type LoadState = "loading" | "loaded" | "error";

/** Everything the panel + detail need to render + drive the caller's own connections. */
export interface MyConnectionsController {
  allowedTypes: AllowedConnectorType[];
  connections: Connection[];
  loadState: LoadState;
  busyId: string | null;
  notice: string | null;
  reload: (silent?: boolean) => Promise<void>;
  test: (connection: Connection) => Promise<void>;
  sync: (connection: Connection) => Promise<void>;
  disconnect: (connection: Connection) => Promise<void>;
}

const GENERIC_MESSAGE = "Something went wrong. Please try again.";

/** HTTP status carried by an AuthRequestError, or 0 for a non-HTTP failure. */
function statusOf(error: unknown): number {
  return error instanceof AuthRequestError ? error.status : 0;
}

/**
 * Manage the caller's own connector connections (Tier 3).
 *
 * Contract: loads the allowed types + connections on mount; exposes the lists, load/busy/notice
 * state, and the lifecycle actions. `logout` is called on a 401 (lapsed session) so the parent
 * redirects to /login. A 403 is a policy denial, not a logout.
 */
export function useMyConnections(logout: () => Promise<void>): MyConnectionsController {
  const [allowedTypes, setAllowedTypes] = useState<AllowedConnectorType[]>([]);
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
        const [types, list] = await Promise.all([listAllowedTypes(), listMyConnections()]);
        if (seq !== reloadSeq.current) return; // superseded by a newer reload
        setAllowedTypes(types);
        setConnections(list);
        setLoadState("loaded");
      } catch (error) {
        if (seq !== reloadSeq.current) return;
        if (statusOf(error) === 401) {
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

  // While any connection is actively syncing, silently refresh so the N/M progress advances live;
  // the poll stops the moment none are running (idle/error) — no idle churn.
  const anySyncing = connections.some((connection) => connection.sync_status === "running");
  useEffect(() => {
    if (!anySyncing) return;
    const timer = window.setInterval(() => void reload(true), SYNC_POLL_MS);
    return () => window.clearInterval(timer);
  }, [anySyncing, reload]);

  /** Run a per-connection mutation, then silently reload; a 401 → logout, else a friendly notice. */
  const runAction = useCallback(
    async (connectionId: string, action: () => Promise<unknown>): Promise<void> => {
      setNotice(null);
      setBusyId(connectionId);
      try {
        await action();
        await reload(true);
      } catch (error) {
        if (statusOf(error) === 401) {
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
    (connection: Connection) => runAction(connection.id, () => testMyConnection(connection.id)),
    [runAction],
  );

  /** Trigger a sync; a 409 (already running) is a no-op — the reload reflects reality. */
  const sync = useCallback(
    (connection: Connection) =>
      runAction(connection.id, async () => {
        try {
          await startMySync(connection.id);
        } catch (error) {
          if (statusOf(error) === 409) return;
          throw error;
        }
      }),
    [runAction],
  );

  const disconnect = useCallback(
    (connection: Connection) =>
      runAction(connection.id, () => disconnectMyConnection(connection.id)),
    [runAction],
  );

  return { allowedTypes, connections, loadState, busyId, notice, reload, test, sync, disconnect };
}
