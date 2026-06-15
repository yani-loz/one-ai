/**
 * Role: The Tier-2 connector governance SCREEN (CO-01) — a company-admin's full-page "Connector
 *       access" console hosting the ConnectorGovernancePanel (org-wide toggle + per-user matrix +
 *       §7 health roll-up). Separate from the user console (/admin) so the user list renders once.
 * Used by: App.tsx (the /admin/connectors route, behind AdminRoute).
 * Depends on: react-router-dom (Link), motion, ../identity (useAuth), ../components/BrandMark,
 *             ./ConnectorGovernancePanel.
 * Key invariants:
 *   - Rendered only for an authenticated company_admin (AdminRoute gates it); org scope is JWT-
 *     derived + RLS-enforced. The panel is metadata-only (§7) — never a mailbox/secret/content.
 *   - The pilot governs IMAP only; a second connector type is just another mounted panel here.
 */
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import { BrandMark } from "../components/BrandMark";
import { useAuth } from "../identity";
import { ConnectorGovernancePanel } from "./ConnectorGovernancePanel";

export function ConnectorGovernancePage(): React.JSX.Element {
  const { user } = useAuth();

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
              <h1 className="text-h2 font-bold text-text-primary">Connector access</h1>
              <p className="text-sm text-text-secondary">
                Who in your company can connect each source
                {user?.org_name ? ` · ${user.org_name}` : ""}
              </p>
            </div>
          </div>
          <Link
            to="/admin"
            state={{ nav: "back" }}
            className="rounded-xl border border-white/50 bg-white/50 px-4 py-2.5 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
          >
            ‹ Back to organisation
          </Link>
        </header>

        <p className="mt-6 text-xs text-text-muted">
          You decide who may connect each source. You see connection health, never anyone&apos;s
          mailbox or its contents — each person connects their own mailbox themselves.
        </p>

        <div className="mt-6 space-y-6">
          <ConnectorGovernancePanel connectorType="imap" />
        </div>
      </div>
    </motion.main>
  );
}
