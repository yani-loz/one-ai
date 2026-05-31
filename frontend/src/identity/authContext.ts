/**
 * Role: The React context object + its value type for the Identity auth state.
 *       Split out from AuthProvider.tsx so the provider file exports only a
 *       component (keeps React Fast Refresh / HMR boundaries clean).
 * Used by: AuthProvider.tsx (creates the Provider), useAuth.ts (reads the context).
 * Depends on: ./types (AuthUser).
 * Key invariants: default value is null so useAuth can detect "used outside provider".
 */
import { createContext } from "react";

import type { AuthUser } from "./types";

/** Lifecycle of the auth session as the UI observes it. */
export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

/** Public surface of the auth context consumed via useAuth. */
export interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  platformLogin: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

/** Null default lets useAuth throw a loud error when used outside <AuthProvider>. */
export const AuthContext = createContext<AuthContextValue | null>(null);
