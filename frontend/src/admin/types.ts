/**
 * Role: Shared TypeScript contracts for the Company Admin console — mirrors the backend
 *       company user-management API shapes (/users/*) exactly.
 * Used by: adminClient.ts, userValidation.ts, UsersTable.tsx, CreateUserDrawer.tsx,
 *          ConfirmDialog.tsx, AdminConsolePage.tsx.
 * Depends on: nothing (leaf module — pure types).
 * Key invariants:
 *   - CompanyUserRole has ONLY the two values the backend UserRole enum + the /users
 *     create/patch surface accept ('company_admin' | 'member'); this surface can never
 *     mint a platform_admin (the backend rejects it, and the type forbids selecting it).
 *   - CompanyUser mirrors the backend UserResponse 1:1 (has is_active + created_at, NO
 *     org_name) — distinct from identity's AuthUser, which carries org_name but not those.
 *   - Request payloads NEVER carry org_id: the backend derives it from the verified token,
 *     and extra='forbid' would 422 a stray field.
 */

/** The two roles a company user can hold (mirrors the backend UserRole enum). */
export type CompanyUserRole = "company_admin" | "member";

/** A company user as returned by GET/POST/PATCH /users (the backend UserResponse). */
export interface CompanyUser {
  id: string;
  email: string;
  full_name: string;
  role: CompanyUserRole;
  is_active: boolean;
  org_id: string;
  created_at: string;
}

/** Payload for POST /users — the admin sets the initial password (no invite flow yet). */
export interface CreateUserRequest {
  email: string;
  full_name: string;
  role: CompanyUserRole;
  password: string;
}

/** Payload for PATCH /users/{id} — every field optional (partial update). */
export interface UpdateUserRequest {
  full_name?: string;
  role?: CompanyUserRole;
  is_active?: boolean;
}
