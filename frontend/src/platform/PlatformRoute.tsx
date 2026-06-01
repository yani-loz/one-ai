/**
 * Role: Route guard for the platform console — renders its children only for an
 *       authenticated PLATFORM ADMIN; routes company users to "/" and anonymous
 *       visitors to /login, and waits (skeleton) while the session bootstraps.
 * Used by: App.tsx (wraps the /platform route).
 * Depends on: ../identity (useAuth), react-router-dom (Navigate).
 * Key invariants:
 *   - The role check here is a UX gate ONLY; real authorization is enforced server-side
 *     by the aud='platform' JWT claim, so a spoofed client role still gets 401s from the
 *     API (it can reach the shell but no platform data).
 *   - While status === "loading" it MUST NOT redirect (avoids a /login flicker on a hard
 *     refresh, during which a platform session — synthesised, no rehydrate — resolves to
 *     unauthenticated and correctly falls back to /login).
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../identity";

/**
 * Gate a subtree behind an authenticated platform-admin session.
 *
 * Contract: `loading` → ambient skeleton (no redirect); `unauthenticated` → /login;
 * authenticated non-platform user → "/" (their own home); platform admin → children.
 */
export function PlatformRoute({ children }: { children: ReactNode }): React.JSX.Element {
  const { status, user } = useAuth();

  if (status === "loading") {
    return (
      <div
        className="flex min-h-screen items-center justify-center"
        role="status"
        aria-label="Restoring session"
      >
        <div className="h-16 w-16 animate-aura-pulse rounded-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-purple blur-md" />
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/login" replace />;
  }

  if (user?.role !== "platform_admin") {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
