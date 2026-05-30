/**
 * Role: Root application shell — a living status panel that proves the full stack
 *       is wired (frontend -> backend /health -> Postgres) on the One AI design system.
 * Used by: src/main.tsx.
 * Depends on: motion (entrance animation), the Tailwind aurora theme (index.css),
 *             import.meta.env.VITE_API_URL.
 */
import { useEffect, useState } from "react";
import { motion } from "motion/react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type HealthState = "checking" | "online" | "offline";

interface HealthResponse {
  service: string;
  version: string;
  database: string;
}

export function App() {
  const [state, setState] = useState<HealthState>("checking");
  const [detail, setDetail] = useState<string>("contacting backend…");

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/health`)
      .then((response) =>
        response.ok ? (response.json() as Promise<HealthResponse>) : Promise.reject(response.status),
      )
      .then((body) => {
        if (cancelled) return;
        setState("online");
        setDetail(`${body.service} v${body.version} · DB ${body.database}`);
      })
      .catch(() => {
        if (cancelled) return;
        setState("offline");
        setDetail("backend unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen flex items-center justify-center p-10">
      <motion.section
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className="relative w-full max-w-md rounded-xl border border-white/50 bg-white/65 p-8 shadow-sm backdrop-blur-xl"
      >
        <div className="relative mx-auto mb-6 h-16 w-16">
          <div className="absolute inset-0 animate-aura-pulse rounded-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-purple blur-md" />
          <div className="absolute inset-2 rounded-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-purple" />
        </div>

        <h1 className="text-center text-h2 font-bold text-brand-gradient">One AI</h1>
        <p className="mt-1 text-center text-sm text-text-secondary">One Company. One AI.</p>

        <div className="mt-6 flex items-center justify-center gap-2 text-sm">
          <StatusDot state={state} />
          <span className="text-text-secondary" data-testid="health-detail">
            {detail}
          </span>
        </div>
      </motion.section>
    </main>
  );
}

function StatusDot({ state }: { state: HealthState }) {
  const tone =
    state === "online"
      ? "bg-brand-teal animate-pulse-dot"
      : state === "offline"
        ? "bg-brand-red"
        : "bg-brand-purple animate-pulse-dot";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${tone}`} aria-label={state} />;
}
