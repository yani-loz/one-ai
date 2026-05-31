/**
 * Role: Route guard — renders its children only for an authenticated session,
 *       redirects to /login otherwise, and waits (renders a skeleton) while the
 *       mount-time bootstrap is still resolving.
 * Used by: App.tsx (wraps the protected HomePage route).
 * Depends on: ./useAuth, react-router-dom (Navigate).
 * Key invariants: while `status === "loading"` it MUST NOT redirect — redirecting
 *   during bootstrap causes a /login flicker on every hard refresh.
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "./useAuth";

/**
 * Gate a subtree behind authentication.
 *
 * Contract: `loading` -> ambient skeleton (no redirect); `authenticated` ->
 * children; `unauthenticated` -> <Navigate> to /login (replace, so the guarded
 * URL does not linger in history).
 */
export function ProtectedRoute({ children }: { children: ReactNode }): React.JSX.Element {
  const { status } = useAuth();

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

  return <>{children}</>;
}
