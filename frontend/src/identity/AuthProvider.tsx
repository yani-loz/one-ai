/**
 * Role: React auth provider for the Identity module — holds the current user and
 *       auth status, exposes login/platformLogin/logout, and bootstraps the session
 *       on mount via the stored refresh token (GET /auth/me with auto-refresh).
 * Used by: main.tsx (wraps the app); its context is consumed via useAuth.ts.
 * Depends on: ./authContext (the context object), ./authClient, ./types.
 * Key invariants:
 *   - `status` is `loading` until the mount-time bootstrap resolves; ProtectedRoute
 *     must wait on `loading` and NOT redirect, or a hard refresh flickers to /login.
 *   - The access token is never stored here (authClient owns it, in memory).
 *   - Platform admins are synthesised in memory (no /platform/me) and do not survive
 *     a hard refresh — they re-login (acceptable per spec: no rehydrate endpoint). The
 *     synthesised identity (incl. role) is DISPLAY-ONLY; never gate authorization on it
 *     (the server enforces access via the JWT aud='platform' claim).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  fetchCurrentUser,
  getStoredRefreshToken,
  login as loginRequest,
  logout as logoutRequest,
  platformLogin as platformLoginRequest,
} from "./authClient";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./authContext";
import type { AuthUser } from "./types";

/**
 * Build the minimal in-memory identity for a platform admin (no /platform/me).
 *
 * SECURITY: this identity — including `role` — is DISPLAY-ONLY, derived from the typed
 * email, not a verified server response. It must never back an authorization decision;
 * access control is enforced server-side by the JWT aud='platform' claim. A real
 * GET /platform/me (docs/FIX_BEFORE_PROD.md) should replace this synthesis (AUD-14).
 */
function synthesizePlatformAdmin(email: string): AuthUser {
  const namePart = email.split("@")[0] ?? email;
  return {
    id: "platform-admin",
    email,
    full_name: namePart,
    role: "platform_admin",
    org_id: null,
    org_name: null,
  };
}

/**
 * Provide the auth context to the tree and run the mount-time session bootstrap.
 *
 * Contract: renders children immediately; consumers read `status` to know whether
 * the session is still resolving. On mount, if a refresh token is stored it calls
 * /auth/me (which auto-refreshes once on 401); any failure resolves to
 * `unauthenticated`. With no stored token it short-circuits to `unauthenticated`
 * with no network call.
 */
export function AuthProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  // Set once an explicit login wins, so a slower mount-time bootstrap cannot overwrite
  // the freshly-authenticated user with the prior stored session (AUD-12).
  const bootstrapSuperseded = useRef(false);

  useEffect(() => {
    let cancelled = false;

    if (getStoredRefreshToken() === null) {
      setStatus("unauthenticated");
      return;
    }

    fetchCurrentUser()
      .then((currentUser) => {
        if (cancelled || bootstrapSuperseded.current) return;
        setUser(currentUser);
        setStatus("authenticated");
      })
      .catch(() => {
        if (cancelled || bootstrapSuperseded.current) return;
        setUser(null);
        setStatus("unauthenticated");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    bootstrapSuperseded.current = true;
    const currentUser = await loginRequest(email, password);
    setUser(currentUser);
    setStatus("authenticated");
  }, []);

  const platformLogin = useCallback(async (email: string, password: string): Promise<void> => {
    bootstrapSuperseded.current = true;
    await platformLoginRequest(email, password);
    setUser(synthesizePlatformAdmin(email));
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    await logoutRequest();
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, platformLogin, logout }),
    [user, status, login, platformLogin, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
