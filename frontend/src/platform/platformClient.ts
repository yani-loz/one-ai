/**
 * Role: Thin HTTP client for the Platform backend (/platform/*) — lists companies and
 *       onboards new ones, attaching the platform admin's Bearer token via the shared
 *       authorizedFetch.
 * Used by: PlatformConsolePage.tsx (list) and OnboardCompanyDrawer.tsx (onboard).
 * Depends on: ../identity (authorizedFetch, AuthRequestError), ./types,
 *             import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - All calls go through authorizedFetch, so the in-memory access token is attached
 *     and a 401 triggers one refresh attempt (token logic stays owned by authClient).
 *   - GET /platform/orgs returns METADATA ONLY — this client never requests tenant
 *     content, mirroring the backend's content-blind platform domain.
 *   - Refresh-on-401 is domain-aware (authClient.performRefresh): a platform session
 *     rotates via POST /platform/refresh using the in-memory platform token, so the
 *     session survives in-tab — but NOT a hard refresh (platform tokens are in-memory
 *     only by design). A genuinely lapsed session surfaces as a re-login.
 */
import { authorizedFetch, AuthRequestError } from "../identity";
import type { OnboardCompanyRequest, OnboardedCompany, OrganizationSummary } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * List every company as operational metadata via GET /platform/orgs.
 *
 * Contract: returns the orgs ordered as the backend returns them (by created_at).
 * Throws AuthRequestError on a non-2xx response (401 = session expired/not a platform
 * admin → the caller routes back to /login).
 */
export async function listOrganizations(): Promise<OrganizationSummary[]> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs`);
  return parseJsonOrThrow<OrganizationSummary[]>(response);
}

/**
 * Onboard a new company plus its first company_admin via POST /platform/orgs.
 *
 * Contract: `payload` must already satisfy the field bounds (the form validates them);
 * the backend re-validates and creates the org + admin atomically. Returns the new org
 * metadata and the seed admin. Throws AuthRequestError — notably status 409 when the
 * slug or admin email is already taken — which the drawer maps to a friendly message.
 */
export async function onboardCompany(payload: OnboardCompanyRequest): Promise<OnboardedCompany> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow<OnboardedCompany>(response);
}
