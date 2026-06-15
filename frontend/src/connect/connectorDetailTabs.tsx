/**
 * Role: The four read/act tab panels of the connector detail screen (CO-01 §5.1) — Status (health /
 *       last-sync / totals), Connection (connect → test → sync → disconnect+erase), History (the
 *       last sync run the connection exposes), and Settings (the owner's IMAP config, read-only).
 *       Presentation + per-action wiring; the data + mutations come from useMyConnections via props.
 * Used by: ConnectorDetailPage.tsx (renders the active tab).
 * Depends on: react, ./types, the aurora Tailwind theme. The owner sees their OWN params here (not a
 *             §7 violation — it's their mailbox); admins get the metadata-only governance view.
 * Key invariants:
 *   - The Connection tab's actions operate on the caller's OWN connection; disconnect ALSO erases the
 *     ingested data (GDPR Art. 17 raw-tier) — the button label + confirm say so explicitly.
 *   - Sync is disabled while a sync is running or the connection is admin-disabled; every action is
 *     blocked while `busy` (one action at a time).
 */
import { useState } from "react";

import type { Connection } from "./types";

const ACTION_CLASS =
  "rounded-lg border border-white/50 bg-white/60 px-3 py-1.5 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100";

/** A small label/value row used across the Status + Settings tabs. */
function InfoRow({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/40 py-2 last:border-b-0">
      <span className="text-sm text-text-secondary">{label}</span>
      <span className="text-sm font-medium text-text-primary">{value}</span>
    </div>
  );
}

/** Format an ISO timestamp for display, or an em-dash when absent. */
function formatDate(iso: string | null): string {
  return iso === null ? "—" : new Date(iso).toLocaleString();
}

/** Status tab: connected?/health/last-sync/totals for the caller's own connection. */
export function StatusTab({ connection }: { connection: Connection | null }): React.JSX.Element {
  if (connection === null) {
    return (
      <p className="text-sm text-text-secondary">
        Not connected yet. Use the Connection tab to connect your mailbox.
      </p>
    );
  }
  const health = !connection.is_enabled
    ? "Disabled by your administrator"
    : connection.status === "connected"
      ? "Connected"
      : connection.status === "error"
        ? "Error"
        : "Not yet verified";
  const total = connection.total_count;
  const progress =
    connection.sync_status === "running"
      ? total !== null
        ? `Syncing… ${connection.synced_count} / ${total}`
        : `Syncing… ${connection.synced_count}`
      : `${connection.synced_count.toLocaleString()} messages`;
  return (
    <div>
      <InfoRow label="Health" value={health} />
      <InfoRow label="Sync state" value={progress} />
      <InfoRow label="Last synced" value={formatDate(connection.last_synced_at)} />
      {connection.last_sync_error !== null && (
        <p className="mt-2 text-xs text-brand-red">Last sync error — {connection.last_sync_error}</p>
      )}
    </div>
  );
}

/** Connection (Actions) tab: connect / test / sync / disconnect+erase the caller's own mailbox. */
export function ConnectionTab({
  connection,
  busy,
  onConnect,
  onTest,
  onSync,
  onDisconnect,
}: {
  connection: Connection | null;
  busy: boolean;
  onConnect: () => void;
  onTest: () => void;
  onSync: () => void;
  onDisconnect: () => void;
}): React.JSX.Element {
  const [confirmingErase, setConfirmingErase] = useState(false);

  if (connection === null) {
    return (
      <div className="text-center">
        <p className="mb-4 text-sm text-text-secondary">
          Connect your mailbox to let your personal AI learn from it. You&apos;ll review and accept a
          short consent first.
        </p>
        <button
          type="button"
          onClick={onConnect}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-5 py-2.5 text-sm font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98]"
        >
          Connect my mailbox
        </button>
      </div>
    );
  }

  const syncing = connection.sync_status === "running";
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
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
        {busy && (
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-brand-teal/30 border-t-brand-teal"
          />
        )}
      </div>

      <div className="mt-6 rounded-xl border border-brand-red/30 bg-brand-red/5 p-4">
        <p className="text-sm font-medium text-text-primary">Disconnect & erase my data</p>
        <p className="mt-1 text-xs text-text-muted">
          Removes this connection and permanently erases the email it ingested (GDPR right to
          erasure). This cannot be undone.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {confirmingErase ? (
            <>
              <span className="text-xs text-text-secondary">Erase this connection &amp; its data?</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirmingErase(false);
                  onDisconnect();
                }}
                className={`${ACTION_CLASS} hover:text-brand-red`}
              >
                Yes, disconnect &amp; erase
              </button>
              <button
                type="button"
                onClick={() => setConfirmingErase(false)}
                className={ACTION_CLASS}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmingErase(true)}
              className={`${ACTION_CLASS} hover:text-brand-red`}
            >
              Disconnect &amp; erase
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** History tab: the caller's last sync run as the connection exposes it (no full runs endpoint yet). */
export function HistoryTab({ connection }: { connection: Connection | null }): React.JSX.Element {
  if (connection === null || connection.last_synced_at === null) {
    return <p className="text-sm text-text-secondary">No sync runs yet.</p>;
  }
  return (
    <div className="animate-fade-in rounded-xl border border-white/50 bg-white/50 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-text-primary">Last sync</span>
        <span className="text-xs text-text-muted">{formatDate(connection.last_synced_at)}</span>
      </div>
      <p className="mt-1 text-xs text-text-secondary">
        {connection.synced_count.toLocaleString()} messages synced
        {connection.last_sync_error !== null ? ` · error: ${connection.last_sync_error}` : ""}
      </p>
    </div>
  );
}

/** Settings tab: the owner's IMAP config (read-only display — change it by reconnecting for now). */
export function SettingsTab({ connection }: { connection: Connection | null }): React.JSX.Element {
  if (connection === null) {
    return (
      <p className="text-sm text-text-secondary">
        Your mailbox settings appear here once you connect.
      </p>
    );
  }
  return (
    <div>
      <InfoRow label="Mailbox" value={connection.username} />
      <InfoRow label="IMAP host" value={connection.host} />
      <InfoRow label="Port" value={String(connection.port)} />
      <InfoRow label="SSL" value={connection.use_ssl ? "On" : "Off"} />
      <p className="mt-3 text-xs text-text-muted">
        To change these, disconnect and reconnect your mailbox.
      </p>
    </div>
  );
}
