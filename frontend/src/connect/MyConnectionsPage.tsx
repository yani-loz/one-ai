/**
 * Role: The Tier-3 "My Connections" panel (CO-01 §5.1) — every authenticated user (member OR admin)
 *       sees a grid of connector-type cards driven by GET /me/connectors/types: only the types they
 *       are allowed to self-connect, each card showing connected? + health + records-synced, linking
 *       to that type's detail. Presentation only; the data lives in useMyConnections.
 * Used by: App.tsx (the /connections route, behind ProtectedRoute — NOT AdminRoute).
 * Depends on: ../identity (useAuth), ../components/BrandMark, ./useMyConnections, ./ConnectorCard,
 *             ./connectorCatalog, ./types, react-router-dom (Link), motion.
 * Key invariants:
 *   - Rendered for ANY authenticated user; the connections shown are the caller's OWN only (server-
 *     enforced by owner-scoping — another user's connection is a 404). This is the personal plane.
 *   - A not-allowed type is rendered LOCKED with its reason (never hidden silently) so the user sees
 *     why a source is unavailable ("your administrator hasn't enabled this").
 */
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import { BrandMark } from "../components/BrandMark";
import { useAuth } from "../identity";
import { ConnectorCard } from "./ConnectorCard";
import type { Connection } from "./types";
import { useMyConnections } from "./useMyConnections";

/** A shimmer placeholder card shown while the panel loads (no spinners). */
function SkeletonCard(): React.JSX.Element {
  return (
    <div className="relative h-[120px] overflow-hidden rounded-xl border border-white/50 bg-white/40">
      <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
    </div>
  );
}

/** Find the caller's own connection for a connector type (or null if not connected). */
function connectionFor(connections: Connection[], type: string): Connection | null {
  return connections.find((connection) => connection.connector_type === type) ?? null;
}

export function MyConnectionsPage(): React.JSX.Element {
  const { user, logout } = useAuth();
  const controller = useMyConnections(logout);

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
              <h1 className="text-h2 font-bold text-text-primary">My connections</h1>
              <p className="text-sm text-text-secondary">
                Sources your personal AI learns from{user?.org_name ? ` · ${user.org_name}` : ""}
              </p>
            </div>
          </div>
          <Link
            to="/"
            className="rounded-xl border border-white/50 bg-white/50 px-4 py-2.5 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
          >
            Home
          </Link>
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
          className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2"
          aria-busy={controller.loadState === "loading"}
          aria-label="Available connectors"
        >
          {controller.loadState === "loading" && (
            <>
              <SkeletonCard />
              <SkeletonCard />
            </>
          )}

          {controller.loadState === "loaded" &&
            controller.allowedTypes.map((allowed) => (
              <ConnectorCard
                key={allowed.connector_type}
                allowed={allowed}
                connection={connectionFor(controller.connections, allowed.connector_type)}
              />
            ))}
        </section>

        {controller.loadState === "error" && (
          <div className="animate-fade-in mt-8 rounded-xl border border-brand-red/30 bg-brand-red/10 p-6 text-center">
            <p className="text-sm text-brand-red">
              Couldn&apos;t load your connectors. Check the API is running.
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

        {controller.loadState === "loaded" && controller.allowedTypes.length === 0 && (
          <div className="animate-fade-in mt-8 rounded-xl border border-white/50 bg-white/40 p-10 text-center">
            <p className="text-text-secondary">
              No connectors are available to you yet. Your administrator enables them.
            </p>
          </div>
        )}
      </div>
    </motion.main>
  );
}
