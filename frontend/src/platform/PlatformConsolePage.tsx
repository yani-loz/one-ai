/**
 * Role: The platform-admin console — the governance control plane's landing screen.
 *       Lists every customer company as a crest gallery (GET /platform/orgs), surfaces
 *       fleet summary stats, lets the admin search/filter and onboard a new company, and
 *       keeps the "content is sealed" guarantee in view.
 * Used by: App.tsx (the /platform route, behind PlatformRoute).
 * Depends on: ../identity (useAuth, AuthRequestError), ./platformClient, ./CompanyCard,
 *             ./OnboardCompanyDrawer, ./SealedBanner, ../components/BrandMark, motion.
 * Key invariants:
 *   - Rendered only for an authenticated platform admin (PlatformRoute gates it); a 401
 *     while loading means the session lapsed → logout, and the guard routes to /login.
 *   - Shows operational metadata only — never tenant content (mirrors the backend).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";

import { BrandMark } from "../components/BrandMark";
import { AuthRequestError, useAuth } from "../identity";
import { CompanyCard } from "./CompanyCard";
import { listOrganizations } from "./platformClient";
import { OnboardCompanyDrawer } from "./OnboardCompanyDrawer";
import { SealedBanner } from "./SealedBanner";
import type { OrganizationSummary } from "./types";

type LoadState = "loading" | "loaded" | "error";
type StatusFilter = "all" | "active" | "suspended";

/** A single labelled fleet metric in the summary row. */
function SummaryStat({ label, value }: { label: string; value: number }): React.JSX.Element {
  return (
    <div className="rounded-xl border border-white/50 bg-white/65 px-4 py-3 text-center shadow-sm backdrop-blur-xl">
      <p className="text-h3 font-bold text-text-primary">{value}</p>
      <p className="text-xs text-text-muted">{label}</p>
    </div>
  );
}

/** A shimmer placeholder card shown while the company list loads (no spinners). */
function SkeletonCard(): React.JSX.Element {
  return (
    <div className="relative h-[76px] overflow-hidden rounded-xl border border-white/50 bg-white/40">
      <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
    </div>
  );
}

export function PlatformConsolePage(): React.JSX.Element {
  const { user, logout } = useAuth();
  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const loadOrganizations = useCallback(async (): Promise<void> => {
    setLoadState("loading");
    try {
      const list = await listOrganizations();
      setOrganizations(list);
      setLoadState("loaded");
    } catch (error) {
      // A lapsed/invalid session (401) → clear it; the route guard sends us to /login.
      if (error instanceof AuthRequestError && error.status === 401) {
        await logout();
        return;
      }
      setLoadState("error");
    }
  }, [logout]);

  useEffect(() => {
    void loadOrganizations();
  }, [loadOrganizations]);

  const stats = useMemo(() => {
    const seats = organizations.reduce((sum, org) => sum + org.user_count, 0);
    const active = organizations.filter((org) => org.status.toLowerCase() === "active").length;
    const suspended = organizations.filter(
      (org) => org.status.toLowerCase() === "suspended",
    ).length;
    return { total: organizations.length, active, suspended, seats };
  }, [organizations]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return organizations.filter((org) => {
      const matchesQuery =
        needle === "" ||
        org.name.toLowerCase().includes(needle) ||
        org.slug.toLowerCase().includes(needle);
      const matchesStatus = statusFilter === "all" || org.status.toLowerCase() === statusFilter;
      return matchesQuery && matchesStatus;
    });
  }, [organizations, query, statusFilter]);

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="min-h-screen px-6 py-10"
    >
      <div className="mx-auto max-w-4xl">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <BrandMark size={44} className="" assembleSeconds={0} />
            <div>
              <h1 className="text-h2 font-bold text-text-primary">Companies</h1>
              <p className="text-sm text-text-secondary">
                {user?.email} · Platform admin
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded-xl border border-white/50 bg-white/50 px-4 py-2.5 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
            >
              Log out
            </button>
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-5 py-2.5 text-sm font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98]"
            >
              + Add company
            </button>
          </div>
        </header>

        <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SummaryStat label="Companies" value={stats.total} />
          <SummaryStat label="Active" value={stats.active} />
          <SummaryStat label="Suspended" value={stats.suspended} />
          <SummaryStat label="Total seats" value={stats.seats} />
        </section>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name or slug…"
            aria-label="Search companies"
            className="min-w-0 flex-1 rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-4 py-2.5 text-sm text-text-primary outline-none transition-[border-color,box-shadow] duration-200 placeholder:text-text-muted focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)]"
          />
          <div className="flex gap-1.5" role="group" aria-label="Filter by status">
            {(["all", "active", "suspended"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={statusFilter === option}
                onClick={() => setStatusFilter(option)}
                className={`rounded-lg border px-3 py-2 text-xs font-medium capitalize transition-all duration-200 ${
                  statusFilter === option
                    ? "border-brand-teal/50 bg-brand-teal/10 text-brand-teal"
                    : "border-white/50 bg-white/50 text-text-secondary hover:border-brand-teal/40"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        <section className="mt-5 space-y-3">
          {loadState === "loading" && (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
            </>
          )}

          {loadState === "error" && (
            <div className="animate-fade-in rounded-xl border border-brand-red/30 bg-brand-red/10 p-6 text-center">
              <p className="text-sm text-brand-red">
                Couldn&apos;t load companies. Check the API is running.
              </p>
              <button
                type="button"
                onClick={() => void loadOrganizations()}
                className="mt-3 rounded-lg border border-white/50 bg-white/60 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
              >
                Retry
              </button>
            </div>
          )}

          {loadState === "loaded" && filtered.length === 0 && (
            <div className="animate-fade-in rounded-xl border border-white/50 bg-white/40 p-8 text-center">
              <p className="text-sm text-text-secondary">
                {organizations.length === 0
                  ? "No companies yet. Onboard the first one to get started."
                  : "No companies match your search."}
              </p>
            </div>
          )}

          {loadState === "loaded" &&
            filtered.map((company) => <CompanyCard key={company.id} company={company} />)}
        </section>

        <div className="mt-8">
          <SealedBanner />
        </div>
      </div>

      <OnboardCompanyDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onOnboarded={() => void loadOrganizations()}
        onSessionExpired={() => void logout()}
      />
    </motion.main>
  );
}
