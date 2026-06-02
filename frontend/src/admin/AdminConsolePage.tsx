/**
 * Role: The company-admin console — the customer's own governance landing screen. Lists the
 *       organization's users (GET /users), shows seat stats, and lets a company_admin add a
 *       user, change roles, and deactivate/reactivate — plus the break-glass approval inbox.
 *       Presentation only; the data + mutation logic lives in useCompanyUsers.
 * Used by: App.tsx (the /admin route, behind AdminRoute).
 * Depends on: ../identity (useAuth), ../components/BrandMark, ../support (SupportInbox),
 *             ./useCompanyUsers, ./UsersTable, ./CreateUserDrawer, ./ConfirmDialog,
 *             react-router-dom (Link), motion.
 * Key invariants:
 *   - Rendered only for an authenticated company_admin (AdminRoute gates it); the server is
 *     the real authority — the org is derived from the JWT, and a cross-org target 404s.
 *   - METADATA ONLY: this surface shows people + roles, never Tier-1 personal intelligence
 *     (conversations/profiles) and never costs/tokens/provider names (Project_Bible §7/§14).
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import { BrandMark } from "../components/BrandMark";
import { useAuth } from "../identity";
import { SupportInbox } from "../support";
import { CreateUserDrawer } from "./CreateUserDrawer";
import { ConfirmDialog } from "./ConfirmDialog";
import { UsersTable } from "./UsersTable";
import { useCompanyUsers } from "./useCompanyUsers";

/** A labelled seat metric in the summary row. */
function SummaryStat({ label, value }: { label: string; value: number }): React.JSX.Element {
  return (
    <div className="rounded-xl border border-white/50 bg-white/65 px-4 py-3 text-center shadow-sm backdrop-blur-xl">
      <p className="text-h3 font-bold text-text-primary">{value}</p>
      <p className="text-xs text-text-muted">{label}</p>
    </div>
  );
}

/** A shimmer placeholder row shown while the user list loads (no spinners). */
function SkeletonRow(): React.JSX.Element {
  return (
    <div className="relative h-[58px] overflow-hidden rounded-xl border border-white/50 bg-white/40">
      <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
    </div>
  );
}

export function AdminConsolePage(): React.JSX.Element {
  const { user, logout } = useAuth();
  const currentUserId = user?.id ?? "";
  const controller = useCompanyUsers(currentUserId, logout);

  const [query, setQuery] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === "") return controller.users;
    return controller.users.filter(
      (candidate) =>
        candidate.full_name.toLowerCase().includes(needle) ||
        candidate.email.toLowerCase().includes(needle),
    );
  }, [controller.users, query]);

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
              <h1 className="text-h2 font-bold text-text-primary">Your organisation</h1>
              <p className="text-sm text-text-secondary">
                {user?.email}
                {user?.org_name !== null && user?.org_name !== undefined
                  ? ` · ${user.org_name}`
                  : ""}
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
              + Add user
            </button>
          </div>
        </header>

        <section className="mt-8 grid grid-cols-3 gap-3">
          <SummaryStat label="Users" value={controller.stats.total} />
          <SummaryStat label="Active" value={controller.stats.active} />
          <SummaryStat label="Administrators" value={controller.stats.admins} />
        </section>

        <div className="mt-8">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name or email…"
            aria-label="Search users"
            className="w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-4 py-2.5 text-sm text-text-primary outline-none transition-[border-color,box-shadow] duration-200 placeholder:text-text-muted focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)]"
          />
        </div>

        {controller.notice !== null && (
          <p
            role="alert"
            className="animate-fade-in mt-4 rounded-lg border border-brand-red/30 bg-brand-red/10 px-3 py-2 text-sm text-brand-red"
          >
            {controller.notice}
          </p>
        )}

        <section className="mt-5 space-y-2">
          {controller.loadState === "loading" && (
            <>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </>
          )}

          {controller.loadState === "error" && (
            <div className="animate-fade-in rounded-xl border border-brand-red/30 bg-brand-red/10 p-6 text-center">
              <p className="text-sm text-brand-red">
                Couldn&apos;t load your users. Check the API is running.
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

          {controller.loadState === "loaded" && filtered.length === 0 && (
            <div className="animate-fade-in rounded-xl border border-white/50 bg-white/40 p-8 text-center">
              <p className="text-sm text-text-secondary">
                {controller.users.length === 0
                  ? "No users yet. Add the first one to get started."
                  : "No users match your search."}
              </p>
            </div>
          )}

          {controller.loadState === "loaded" && filtered.length > 0 && (
            <UsersTable
              users={filtered}
              currentUserId={currentUserId}
              busyUserId={controller.busyUserId}
              onChangeRole={controller.changeRole}
              onDeactivate={controller.requestDeactivate}
              onReactivate={controller.reactivate}
            />
          )}
        </section>

        <div className="mt-8">
          <SupportInbox />
        </div>
      </div>

      <CreateUserDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onCreated={() => void controller.reload()}
        onSessionExpired={() => void logout()}
      />

      {controller.confirmCopy !== null && (
        <ConfirmDialog
          open={controller.confirmOpen}
          title={controller.confirmCopy.title}
          message={controller.confirmCopy.message}
          confirmLabel={controller.confirmCopy.confirmLabel}
          tone="danger"
          busy={controller.confirmBusy}
          errorMessage={controller.confirmError}
          onConfirm={() => void controller.confirmPending()}
          onCancel={controller.cancelConfirm}
        />
      )}
    </motion.main>
  );
}
