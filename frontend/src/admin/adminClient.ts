/**
 * Role: Thin HTTP client for the company user-management backend (/users/*) — lists,
 *       creates, updates (role / active), and deactivates users in the caller's own org,
 *       attaching the company_admin's Bearer token via the shared authorizedFetch.
 * Used by: AdminConsolePage.tsx (list + mutations), CreateUserDrawer.tsx (create).
 * Depends on: ../identity (authorizedFetch, AuthRequestError), ./types,
 *             import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - All calls go through authorizedFetch, so the in-memory access token is attached and a
 *     401 triggers one refresh-and-replay (token logic stays owned by authClient). A 2nd
 *     401 surfaces here as AuthRequestError(401) → the caller logs out.
 *   - org scope is NEVER sent: the backend derives org_id from the verified JWT, and the
 *     request models are extra='forbid', so a stray org_id would 422. The cross-tenant
 *     guarantee is server-side (a cross-org target resolves to 404, never an existence leak).
 *   - deactivateCompanyUser hits DELETE which returns 204 with NO body — it must not parse
 *     JSON; success is the 2xx status alone.
 */
import { authorizedFetch, AuthRequestError } from "../identity";
import type { CompanyUser, CreateUserRequest, UpdateUserRequest } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * List every user in the caller's organization via GET /users.
 *
 * Contract: returns users newest-first (as the backend orders them), including DEACTIVATED
 * ones (is_active === false) so the console can offer reactivation. Throws AuthRequestError
 * — 401 = lapsed session / not a company admin → the caller routes back to /login.
 */
export async function listCompanyUsers(): Promise<CompanyUser[]> {
  const response = await authorizedFetch(`${API_URL}/users`);
  return parseJsonOrThrow<CompanyUser[]>(response);
}

/**
 * Create a user in the caller's organization via POST /users.
 *
 * Contract: `payload` must satisfy the field bounds (the form validates them); the backend
 * re-validates and persists. The admin sets the initial password (no invite flow yet — see
 * FIX_BEFORE_PROD). Returns the created user. Throws AuthRequestError — notably 409 when the
 * email already belongs to a user, which the drawer maps to a specific message.
 */
export async function createCompanyUser(payload: CreateUserRequest): Promise<CompanyUser> {
  const response = await authorizedFetch(`${API_URL}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<CompanyUser>(response);
}

/**
 * Apply a partial update to a user via PATCH /users/{id}.
 *
 * Contract: only the provided fields change (role, full_name, and/or is_active). Returns the
 * updated user. Throws AuthRequestError — 404 when the user is not in this org (no existence
 * leak), 409 when the change would strand the org's last active admin (LastAdminError), 401
 * when the session has lapsed.
 */
export async function updateCompanyUser(
  userId: string,
  payload: UpdateUserRequest,
): Promise<CompanyUser> {
  const response = await authorizedFetch(`${API_URL}/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<CompanyUser>(response);
}

/**
 * Deactivate (soft-delete) a user via DELETE /users/{id}.
 *
 * Contract: resolves on a 2xx (the endpoint returns 204 No Content — there is NO body to
 * parse). Throws AuthRequestError — 404 unknown/foreign user, 409 last active admin, 401
 * lapsed session.
 */
export async function deactivateCompanyUser(userId: string): Promise<void> {
  const response = await authorizedFetch(`${API_URL}/users/${userId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
}
