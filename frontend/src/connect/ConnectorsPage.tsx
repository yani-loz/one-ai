/**
 * Role: The Connect console — a company_admin connects mailboxes and manages them (test, enable /
 *       disable, delete). Presentation only; the data + lifecycle logic lives in useConnectors.
 * Used by: App.tsx (the /connectors route, behind AdminRoute).
 * Depends on: ../identity (useAuth), ../components/BrandMark, ./useConnectors, ./AddMailboxDrawer,
 *             ./ConnectorStatusBadge, ./types, react-router-dom (Link), motion.
 * Key invariants:
 *   - Rendered only for an authenticated company_admin (AdminRoute gates it); org scope is
 *     server-derived from the JWT and now DB-enforced (RLS), so a cross-org id 404s.
 *   - Disable is reversible (it sets the connection's disabled_at); delete is destructive and
 *     gated by an inline confirm. One action runs per row at a time (busyId).
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import { BrandMark } from "../components/BrandMark";
import { useAuth } from "../identity";
import { AddMailboxDrawer } from "./AddMailboxDrawer";
import { ConnectorStatusBadge } from "./ConnectorStatusBadge";
import type { Connection } from "./types";
import { useConnectors } from "./useConnectors";

/** A shimmer placeholder shown while the connection list loads (no spinners). */
function SkeletonRow(): React.JSX.Element {
  return (
    <div className="relative h-[72px] overflow-hidden rounded-xl border border-white/50 bg-white/40">
      <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
    </div>
  );
}

const ACTION_CLASS =
  "rounded-lg border border-white/50 bg-white/60 px-3 py-1.5 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100";

/** The live sync line — N/M progress while running, the sync error, or the last-synced time. */
function SyncStatusLine({ connection }: { connection: Connection }): React.JSX.Element | null {
  if (connection.sync_status === "running") {
    const total = connection.total_count;
    const progress =
      total !== null ? `${connection.synced_count} / ${total}` : `${connection.synced_count}`;
    return (
      <div className="mt-2 flex items-center gap-2 text-xs font-medium text-brand-teal">
        <span aria-hidden="true" className="h-2 w-2 animate-pulse-dot rounded-full bg-brand-teal" />
        <span>Syncing… {progress} messages</span>
      </div>
    );
  }
  if (connection.sync_status === "error" && connection.last_sync_error !== null) {
    return <p className="mt-2 text-xs text-brand-red">Sync failed — {connection.last_sync_error}</p>;
  }
  if (connection.last_synced_at !== null) {
    return (
      <p className="mt-2 text-xs text-text-muted">
        Last synced {new Date(connection.last_synced_at).toLocaleString()}
      </p>
    );
  }
  return null;
}

/** One connection as a glass card with its status + lifecycle actions. */
function ConnectorRow({
  connection,
  busy,
  confirmingDelete,
  onTest,
  onSync,
  onToggleEnabled,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: {
  connection: Connection;
  busy: boolean;
  confirmingDelete: boolean;
  onTest: () => void;
  onSync: () => void;
  onToggleEnabled: () => void;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}): React.JSX.Element {
  const syncing = connection.sync_status === "running";
  return (
    <div className="animate-fade-in rounded-xl border border-white/50 bg-white/65 p-4 shadow-sm backdrop-blur-xl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold text-text-primary">{connection.display_name}</p>
          <p className="truncate text-sm text-text-muted">{connection.username}</p>
        </div>
        <ConnectorStatusBadge connection={connection} />
      </div>

      {connection.status === "error" && connection.last_error !== null && (
        <p className="mt-2 text-xs text-brand-red">{connection.last_error}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {confirmingDelete ? (
          <>
            <span className="text-xs text-text-secondary">Delete this connection?</span>
            <button type="button" disabled={busy} onClick={onConfirmDelete} className={ACTION_CLASS}>
              Yes, delete
            </button>
            <button type="button" onClick={onCancelDelete} className={ACTION_CLASS}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy || syncing || !connection.is_enabled}
              onClick={onSync}
              className={`${ACTION_CLASS} hover:text-brand-teal`}
            >
              {syncing ? "Syncing…" : "Sync now"}
            </button>
            <button type="button" disabled={busy} onClick={onTest} className={ACTION_CLASS}>
              Test
            </button>
            <button type="button" disabled={busy} onClick={onToggleEnabled} className={ACTION_CLASS}>
              {connection.is_enabled ? "Disable" : "Enable"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onRequestDelete}
              className={`${ACTION_CLASS} hover:text-brand-red`}
            >
              Delete
            </button>
            {busy && (
              <span
                aria-hidden="true"
                className="h-4 w-4 animate-spin rounded-full border-2 border-brand-teal/30 border-t-brand-teal"
              />
            )}
          </>
        )}
      </div>

      <SyncStatusLine connection={connection} />
    </div>
  );
}

export function ConnectorsPage(): React.JSX.Element {
  const { user, logout } = useAuth();
  const controller = useConnectors(logout);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="min-h-screen px-6 py-10"
    >
      <div className="mx-auto max-w-3xl">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <BrandMark size={44} className="" assembleSeconds={0} />
            <div>
              <h1 className="text-h2 font-bold text-text-primary">Connect your sources</h1>
              <p className="text-sm text-text-secondary">
                Mailboxes One AI learns from{user?.org_name ? ` · ${user.org_name}` : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link
              to="/"
              className="rounded-xl border border-white/50 bg-white/50 px-4 py-2.5 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
            >
              Home
            </Link>
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-5 py-2.5 text-sm font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98]"
            >
              + Connect a mailbox
            </button>
          </div>
        </header>

        {controller.notice !== null && (
          <p
            role="alert"
            className="animate-fade-in mt-6 rounded-lg border border-brand-red/30 bg-brand-red/10 px-3 py-2 text-sm text-brand-red"
          >
            {controller.notice}
          </p>
        )}

        <section
          className="mt-8 space-y-3"
          aria-busy={controller.loadState === "loading"}
          aria-label="Connected mailboxes"
        >
          {controller.loadState === "loading" && (
            <>
              <SkeletonRow />
              <SkeletonRow />
            </>
          )}

          {controller.loadState === "error" && (
            <div className="animate-fade-in rounded-xl border border-brand-red/30 bg-brand-red/10 p-6 text-center">
              <p className="text-sm text-brand-red">
                Couldn&apos;t load your connections. Check the API is running.
              </p>
              <button
                type="button"
                onClick={() => void controller.reload()}
                className="mt-3 rounded-lg border border-white/50 bg-white/60 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
              >
                Retry
              </button>
            </div>
          )}

          {controller.loadState === "loaded" && controller.connections.length === 0 && (
            <div className="animate-fade-in rounded-xl border border-white/50 bg-white/40 p-10 text-center">
              <p className="text-text-secondary">No mailboxes connected yet.</p>
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="mt-4 inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-5 py-2.5 text-sm font-semibold text-white transition-all duration-300 hover:scale-[1.02] active:scale-[0.98]"
              >
                + Connect your first mailbox
              </button>
            </div>
          )}

          {controller.loadState === "loaded" &&
            controller.connections.map((connection) => (
              <ConnectorRow
                key={connection.id}
                connection={connection}
                busy={controller.busyId === connection.id}
                confirmingDelete={confirmDeleteId === connection.id}
                onTest={() => void controller.test(connection)}
                onSync={() => void controller.sync(connection)}
                onToggleEnabled={() =>
                  void controller.setEnabled(connection, !connection.is_enabled)
                }
                onRequestDelete={() => setConfirmDeleteId(connection.id)}
                onConfirmDelete={() => {
                  setConfirmDeleteId(null);
                  void controller.remove(connection);
                }}
                onCancelDelete={() => setConfirmDeleteId(null)}
              />
            ))}
        </section>
      </div>

      <AddMailboxDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onConnected={() => void controller.reload()}
        onSessionExpired={() => void logout()}
      />
    </motion.main>
  );
}
