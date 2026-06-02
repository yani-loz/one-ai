/**
 * Role: Route guard for the company-admin console — renders its children only for an
 *       authenticated COMPANY ADMIN; routes members + platform admins to "/" and anonymous
 *       visitors to /login, and waits (skeleton) while the session bootstraps.
 * Used by: App.tsx (wraps the /admin route).
 * Depends on: ../identity (useAuth), react-router-dom (Navigate).
 * Key invariants:
 *   - The role check here is a UX gate ONLY; real authorization is enforced server-side by
 *     require_company_admin (a member's token gets 403, an anonymous one 401), so a spoofed
 *     client role reaches the shell but no user data.
 *   - While status === "loading" it MUST NOT redirect (avoids a /login flicker on a hard
 *     refresh, during which a company session rehydrates from its stored refresh token).
 *   - A platform admin hitting /admin is sent to "/", where RoleHome forwards them on to
 *     /platform — each role lands on its own home.
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../identity";

/**
 * Gate a subtree behind an authenticated company-admin session.
 *
 * Contract: `loading` → ambient skeleton (no redirect); `unauthenticated` → /login;
 * authenticated non-company-admin (member or platform admin) → "/" (their own home);
 * company admin → children.
 */
export function AdminRoute({ children }: { children: ReactNode }): React.JSX.Element {
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

  if (user?.role !== "company_admin") {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
