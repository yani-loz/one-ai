/**
 * Role: Hook accessor over the auth context — the single supported way for UI to
 *       read the current user/status and call login/logout.
 * Used by: ProtectedRoute.tsx, LoginPage.tsx, HomePage.tsx.
 * Depends on: ./authContext (AuthContext).
 * Key invariants: throws if used outside <AuthProvider>, so a missing provider is a
 *   loud bug at render time rather than a silent null-context read.
 */
import { useContext } from "react";

import { AuthContext, type AuthContextValue } from "./authContext";

/**
 * Read the auth context.
 *
 * Contract: returns the live {user, status, login, platformLogin, logout} value.
 * Edge case: throws Error when called outside an <AuthProvider>.
 */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return context;
}
