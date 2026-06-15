/**
 * Role: Shared TypeScript contracts for the Identity module — mirrors the backend
 *       auth API shapes (AuthenticatedUserResponse, token pair) and auth scopes.
 * Used by: authClient.ts, AuthProvider.tsx, useAuth.ts, LoginPage.tsx, HomePage.tsx.
 * Depends on: nothing (leaf module — pure types).
 * Key invariants: `Role` here matches the backend UserRole enum plus the separate
 *   `platform_admin` scope; `AuthUser.org_id`/`org_name` are null only for platform admins.
 */

/** Identity scopes recognised by the frontend (mirrors backend roles + platform scope). */
export type Role = "company_admin" | "member" | "platform_admin";

/** Which login endpoint / token audience a credential set targets. */
export type AuthScope = "company" | "platform";

/** Authenticated principal as the UI needs it (greeting, role-gating, org context). */
export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  /** Null for platform admins (global scope, not an org row). */
  org_id: string | null;
  /** Null for platform admins; the human-readable org name for company users. */
  org_name: string | null;
}

/** Opaque-refresh + JWT-access pair (PLATFORM login/refresh — token held in memory, AUD-14). */
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
}

/**
 * Company `/auth/login` + `/auth/refresh` response body. The refresh token is NOT here —
 * it rides an httpOnly cookie (Control C), so the body carries only the in-memory access token.
 */
export interface AccessTokenResponse {
  access_token: string;
  token_type: "bearer";
}

/** Raw `/auth/login` response (access token + the authenticated company user; refresh in cookie). */
export interface CompanyLoginResponse extends AccessTokenResponse {
  user: AuthUser;
}

/** The platform admin's own identity, returned by GET /platform/me. */
export interface PlatformAdminView {
  id: string;
  email: string;
  full_name: string;
}
