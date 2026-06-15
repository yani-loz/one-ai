/**
 * Role: Minimal authenticated landing screen — greets the signed-in user (name +
 *       role + org), shows the backend /health status, links EVERY user to their personal
 *       self-connect plane (/connections), links a company_admin to their admin console
 *       (/admin), and offers logout.
 * Used by: src/App.tsx (the protected "/" route).
 * Depends on: ./identity (useAuth), react-router-dom (Link), motion (entrance), the aurora
 *             Tailwind theme, import.meta.env.VITE_API_URL.
 * Key invariants: rendered only inside <ProtectedRoute>, so `user` is non-null here;
 *   the health probe is read-only and unauthenticated (mirrors the original App probe).
 *   "/connections" (the per-user self-connect plane) is shown to ALL roles — it's the
 *   personal plane everyone owns. The "/admin" link is the company_admin's console entry
 *   point — other roles never see it; the connector governance panel lives inside /admin.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import { getApiBaseUrl } from "./api/apiBase";
import { AgentInsignia } from "./components/AgentInsignia";
import { BrandMark } from "./components/BrandMark";
import { seedFromString, type AgentRank } from "./components/insignia/generateInsignia";
import { useAuth } from "./identity";

const API_URL = getApiBaseUrl();

type HealthState = "checking" | "online" | "offline";

interface HealthResponse {
  service: string;
  version: string;
  database: string;
}

/** Human-readable label for each identity role. */
const ROLE_LABELS: Record<string, string> = {
  platform_admin: "Platform admin",
  company_admin: "Company admin",
  member: "Member",
};

/** A user's personal-AI insignia rank reflects their access level. */
const RANK_BY_ROLE: Record<string, AgentRank> = {
  platform_admin: 3,
  company_admin: 2,
  member: 1,
};

export function HomePage(): React.JSX.Element {
  const { user, logout } = useAuth();
  const [healthState, setHealthState] = useState<HealthState>("checking");
  const [healthDetail, setHealthDetail] = useState<string>("contacting backend…");

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/health`)
      .then((response) =>
        response.ok
          ? (response.json() as Promise<HealthResponse>)
          : Promise.reject(response.status),
      )
      .then((body) => {
        if (cancelled) return;
        setHealthState("online");
        setHealthDetail(`${body.service} v${body.version} · DB ${body.database}`);
      })
      .catch(() => {
        if (cancelled) return;
        setHealthState("offline");
        setHealthDetail("backend unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const roleLabel = user !== null ? (ROLE_LABELS[user.role] ?? user.role) : "";

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="flex min-h-screen items-center justify-center p-6"
    >
      <div className="w-full max-w-md">
      <section className="w-full rounded-xl border border-white/50 bg-white/65 p-8 shadow-sm backdrop-blur-xl">
        <BrandMark size={88} className="mx-auto mb-6" />

        <h1 className="text-center text-h3 font-bold text-text-primary">
          Welcome, <span className="text-brand-gradient">{user?.full_name}</span>
        </h1>
        <p className="mt-1 text-center text-sm text-text-secondary">
          {roleLabel}
          {user?.org_name !== null && user?.org_name !== undefined ? ` · ${user.org_name}` : ""}
        </p>

        {user !== null && (
          <div className="mt-6 flex items-center gap-3 rounded-xl border border-white/50 bg-white/40 p-3">
            <AgentInsignia
              seed={seedFromString(user.id)}
              branch="orchestrator"
              rank={RANK_BY_ROLE[user.role] ?? 1}
              size={52}
              assembleSeconds={1.6}
              label={`${user.full_name}'s personal AI`}
              className="shrink-0"
            />
            <div className="min-w-0">
              <p className="text-sm font-medium text-text-primary">Your personal AI</p>
              <p className="text-xs text-text-muted">
                Orchestrator · {"★".repeat(RANK_BY_ROLE[user.role] ?? 1)}
              </p>
            </div>
          </div>
        )}

        <div className="mt-6 flex items-center justify-center gap-2 text-sm">
          <HealthDot state={healthState} />
          <span className="text-text-secondary" data-testid="health-detail">
            {healthDetail}
          </span>
        </div>

        {/* The self-connect plane is universal — every authenticated user (member OR admin) owns
            their personal connections. */}
        <Link
          to="/connections"
          className="mt-8 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-8 py-3 font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98]"
        >
          My connections
        </Link>

        {user?.role === "company_admin" && (
          <Link
            to="/admin"
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-white/50 bg-white/50 px-8 py-3 font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
          >
            Manage organisation
          </Link>
        )}

        <button
          type="button"
          onClick={() => void logout()}
          className="mt-3 inline-flex w-full items-center justify-center rounded-xl border border-white/50 bg-white/50 px-8 py-3 font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
        >
          Log out
        </button>
      </section>
      </div>
    </motion.main>
  );
}

function HealthDot({ state }: { state: HealthState }): React.JSX.Element {
  const tone =
    state === "online"
      ? "bg-brand-teal animate-pulse-dot"
      : state === "offline"
        ? "bg-brand-red"
        : "bg-brand-purple animate-pulse-dot";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${tone}`} aria-label={state} />;
}
