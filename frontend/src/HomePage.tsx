/**
 * Role: Minimal authenticated landing screen — greets the signed-in user (name +
 *       role + org), shows the backend /health status, and offers logout.
 * Used by: src/App.tsx (the protected "/" route).
 * Depends on: ./identity (useAuth), motion (entrance), the aurora Tailwind theme,
 *             import.meta.env.VITE_API_URL.
 * Key invariants: rendered only inside <ProtectedRoute>, so `user` is non-null here;
 *   the health probe is read-only and unauthenticated (mirrors the original App probe).
 */
import { useEffect, useState } from "react";
import { motion } from "motion/react";

import { BrandMark } from "./components/BrandMark";
import { useAuth } from "./identity";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

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
      <section className="w-full max-w-md rounded-xl border border-white/50 bg-white/65 p-8 shadow-sm backdrop-blur-xl">
        <BrandMark size={88} className="mx-auto mb-6" />

        <h1 className="text-center text-h3 font-bold text-text-primary">
          Welcome, <span className="text-brand-gradient">{user?.full_name}</span>
        </h1>
        <p className="mt-1 text-center text-sm text-text-secondary">
          {roleLabel}
          {user?.org_name !== null && user?.org_name !== undefined ? ` · ${user.org_name}` : ""}
        </p>

        <div className="mt-6 flex items-center justify-center gap-2 text-sm">
          <HealthDot state={healthState} />
          <span className="text-text-secondary" data-testid="health-detail">
            {healthDetail}
          </span>
        </div>

        <button
          type="button"
          onClick={() => void logout()}
          className="mt-8 inline-flex w-full items-center justify-center rounded-xl border border-white/50 bg-white/50 px-8 py-3 font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
        >
          Log out
        </button>
      </section>
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
