/**
 * Role: React auth provider for the Identity module — holds the current user and
 *       auth status, exposes login/platformLogin/logout, and bootstraps the session
 *       on mount by always attempting GET /auth/me (auto-refresh on 401 via the httpOnly
 *       refresh cookie, which JS cannot read — so there is no stored token to gate on).
 * Used by: main.tsx (wraps the app); its context is consumed via useAuth.ts.
 * Depends on: ./authContext (the context object), ./authClient, ./types.
 * Key invariants:
 *   - `status` is `loading` until the mount-time bootstrap resolves; ProtectedRoute
 *     must wait on `loading` and NOT redirect, or a hard refresh flickers to /login.
 *   - The access token is never stored here (authClient owns it, in memory).
 *   - Platform admins resolve their REAL identity from GET /platform/me on login (no
 *     more email synthesis). Their tokens live in memory only, so the session does NOT
 *     survive a hard refresh — they re-login (a deliberate security choice: no
 *     high-privilege credential is persisted). The role is still server-authoritative
 *     via the JWT aud='platform' claim; client-side role gates UX only.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  fetchCurrentPlatformAdmin,
  fetchCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  platformLogin as platformLoginRequest,
} from "./authClient";
import { AuthContext, type AuthContextValue, type AuthStatus } from "./authContext";
import type { AuthUser } from "./types";

/**
 * Provide the auth context to the tree and run the mount-time session bootstrap.
 *
 * Contract: renders children immediately; consumers read `status` to know whether
 * the session is still resolving. On mount it always calls /auth/me (which auto-refreshes
 * once on 401 via the httpOnly cookie) — the company refresh token is an httpOnly cookie
 * the JS cannot read, so there is no token to gate on: a present cookie rehydrates the
 * session, its absence (or a platform admin who reloaded — platform tokens are memory-only)
 * resolves to `unauthenticated`.
 */
export function AuthProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  // Set once an explicit login wins, so a slower mount-time bootstrap cannot overwrite
  // the freshly-authenticated user with the prior stored session (AUD-12).
  const bootstrapSuperseded = useRef(false);

  useEffect(() => {
    let cancelled = false;

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
    // Resolve the REAL identity from the server (no more email synthesis) — AUD-14 closed.
    // If /platform/me fails after login already stored an in-memory platform token, tear
    // down that half-open session (best-effort server revoke) before surfacing the error,
    // so no orphaned high-privilege token lingers.
    const admin = await fetchCurrentPlatformAdmin().catch(async (error: unknown) => {
      await logoutRequest();
      throw error;
    });
    setUser({
      id: admin.id,
      email: admin.email,
      full_name: admin.full_name,
      role: "platform_admin",
      org_id: null,
      org_name: null,
    });
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
