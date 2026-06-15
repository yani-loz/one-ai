/**
 * Role: The Tier-3 connector detail screen (CO-01 §5.1, route /connections/:type) — a header (glyph,
 *       label, health/"Syncing…" badge, records-synced) over four tabs (Status / Connection /
 *       History / Settings) for the caller's OWN connection of this type. The consent-gated connect
 *       lives in ConsentModal; the data + actions come from useMyConnections.
 * Used by: App.tsx (the /connections/:type route, behind ProtectedRoute — NOT AdminRoute).
 * Depends on: react, react-router-dom (useParams/useNavigate), motion, ../identity (useAuth),
 *             ./useMyConnections, ./connectorCatalog, ./connectorDetailTabs, ./ConsentModal,
 *             ./ConnectorStatusBadge, ./types.
 * Key invariants:
 *   - Shows ONLY the caller's own connection (owner-scoped server-side). If the :type param isn't an
 *     allowed type for this user, the page shows the friendly denial — never another user's data.
 *   - First connect always goes through ConsentModal (the HITL gate); disconnect erases the data.
 */
import { useState } from "react";
import { motion } from "motion/react";
import { useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../identity";
import { connectorTypeMeta } from "./connectorCatalog";
import { ConnectorStatusBadge } from "./ConnectorStatusBadge";
import { ConsentModal } from "./ConsentModal";
import {
  ConnectionTab,
  HistoryTab,
  SettingsTab,
  StatusTab,
} from "./connectorDetailTabs";
import type { Connection } from "./types";
import { useMyConnections } from "./useMyConnections";

type TabKey = "status" | "connection" | "history" | "settings";

const TABS: { key: TabKey; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "connection", label: "Connection" },
  { key: "history", label: "History" },
  { key: "settings", label: "Settings" },
];

/** Find the caller's own connection for a connector type (or null if not connected). */
function connectionFor(connections: Connection[], type: string): Connection | null {
  return connections.find((connection) => connection.connector_type === type) ?? null;
}

export function ConnectorDetailPage(): React.JSX.Element {
  const { type = "imap" } = useParams<{ type: string }>();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const controller = useMyConnections(logout);
  const [activeTab, setActiveTab] = useState<TabKey>("status");
  const [consentOpen, setConsentOpen] = useState(false);

  const meta = connectorTypeMeta(type);
  const connection = connectionFor(controller.connections, type);
  const allowed = controller.allowedTypes.find((entry) => entry.connector_type === type);
  const busy = connection !== null && controller.busyId === connection.id;

  function goBack(): void {
    navigate("/connections", { state: { nav: "back" } });
  }

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="min-h-screen px-6 py-10"
    >
      <div className="mx-auto max-w-2xl">
        <button
          type="button"
          onClick={goBack}
          className="mb-6 inline-flex items-center gap-1 text-sm font-medium text-text-secondary transition-colors duration-200 hover:text-brand-teal"
        >
          ‹ Back to my connections
        </button>

        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span aria-hidden="true" className="text-3xl">
              {meta.glyph}
            </span>
            <div>
              <h1 className="text-h3 font-bold text-text-primary">{meta.label}</h1>
              {connection !== null && (
                <p className="text-xs text-text-muted">
                  {connection.synced_count.toLocaleString()} messages synced
                </p>
              )}
            </div>
          </div>
          {connection !== null && <ConnectorStatusBadge connection={connection} />}
        </header>

        {controller.notice !== null && (
          <p
            role="alert"
            className="animate-fade-in mt-4 rounded-lg border border-brand-red/30 bg-brand-red/10 px-3 py-2 text-sm text-brand-red"
          >
            {controller.notice}
          </p>
        )}

        {allowed !== undefined && !allowed.allowed ? (
          <div className="animate-fade-in mt-8 rounded-xl border border-white/50 bg-white/40 p-8 text-center">
            <p className="text-text-secondary">🔒 {allowed.reason ?? "This connector isn't available to you."}</p>
          </div>
        ) : (
          <>
            <nav className="mt-6 flex gap-1 border-b border-white/40" aria-label="Connector detail tabs">
              {TABS.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveTab(tab.key)}
                  aria-current={activeTab === tab.key ? "page" : undefined}
                  className={`rounded-t-lg px-4 py-2 text-sm font-medium transition-colors duration-200 ${
                    activeTab === tab.key
                      ? "border-b-2 border-brand-teal text-brand-teal"
                      : "text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>

            <section className="animate-fade-in mt-6 rounded-xl border border-white/50 bg-white/65 p-6 shadow-sm backdrop-blur-xl">
              {activeTab === "status" && <StatusTab connection={connection} />}
              {activeTab === "connection" && (
                <ConnectionTab
                  connection={connection}
                  busy={busy}
                  onConnect={() => setConsentOpen(true)}
                  onTest={() => connection !== null && void controller.test(connection)}
                  onSync={() => connection !== null && void controller.sync(connection)}
                  onDisconnect={() => connection !== null && void controller.disconnect(connection)}
                />
              )}
              {activeTab === "history" && <HistoryTab connection={connection} />}
              {activeTab === "settings" && <SettingsTab connection={connection} />}
            </section>
          </>
        )}
      </div>

      <ConsentModal
        open={consentOpen}
        connectorLabel={meta.label.toLowerCase()}
        onClose={() => setConsentOpen(false)}
        onConnected={() => void controller.reload(true)}
        onSessionExpired={() => void logout()}
      />
    </motion.main>
  );
}
