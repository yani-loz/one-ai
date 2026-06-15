/**
 * Role: Thin HTTP client for the Platform backend (/platform/*) — lists/onboards
 *       companies and reads/updates one company's lifecycle (status + legal hold),
 *       attaching the platform admin's Bearer token via the shared authorizedFetch.
 * Used by: PlatformConsolePage.tsx (list), OnboardCompanyDrawer.tsx (onboard),
 *          OrganizationDetailPage.tsx (detail + status/legal-hold).
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
import { getApiBaseUrl } from "../api/apiBase";
import { authorizedFetch, AuthRequestError } from "../identity";
import type {
  AuditLogEntry,
  ComplianceExport,
  ErasureCertificate,
  OnboardCompanyRequest,
  OnboardedCompany,
  OrganizationDetail,
  OrganizationStatus,
  OrganizationSummary,
} from "./types";

const API_URL = getApiBaseUrl();

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

/**
 * Fetch one company's lifecycle detail via GET /platform/orgs/{id}.
 *
 * Contract: returns the org's metadata + legal hold. Throws AuthRequestError — 404 when
 * no org has that id, 401 when the session has lapsed.
 */
export async function getOrganization(orgId: string): Promise<OrganizationDetail> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs/${orgId}`);
  return parseJsonOrThrow<OrganizationDetail>(response);
}

/**
 * Set a company's lifecycle status via PATCH /platform/orgs/{id}/status.
 *
 * Contract: returns the updated detail. `suspended` blocks that org's company logins
 * server-side. Throws AuthRequestError (404 unknown org, 401 lapsed session).
 */
export async function updateOrganizationStatus(
  orgId: string,
  status: OrganizationStatus,
): Promise<OrganizationDetail> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs/${orgId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return parseJsonOrThrow<OrganizationDetail>(response);
}

/**
 * Set or clear a company's legal hold via PATCH /platform/orgs/{id}/legal-hold.
 *
 * Contract: returns the updated detail. Throws AuthRequestError (404 unknown org, 401
 * lapsed session).
 */
export async function updateOrganizationLegalHold(
  orgId: string,
  legalHold: boolean,
): Promise<OrganizationDetail> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs/${orgId}/legal-hold`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ legal_hold: legalHold }),
  });
  return parseJsonOrThrow<OrganizationDetail>(response);
}

/**
 * Fetch one company's audit trail via GET /platform/orgs/{id}/audit.
 *
 * Contract: returns up to `limit` entries newest-first — metadata about actions
 * (who/what/when/where), never tenant content or secrets. Throws AuthRequestError (404
 * unknown org, 401 lapsed session).
 */
export async function getOrganizationAudit(
  orgId: string,
  limit = 25,
): Promise<AuditLogEntry[]> {
  const response = await authorizedFetch(
    `${API_URL}/platform/orgs/${orgId}/audit?limit=${limit}`,
  );
  return parseJsonOrThrow<AuditLogEntry[]>(response);
}

/**
 * Erase a company's personal data via POST /platform/orgs/{id}/erase.
 *
 * Contract: `confirmSlug` must equal the org's slug and `password` must be the acting
 * admin's own password (sudo-style re-auth) — the backend re-checks both. Returns the
 * deletion certificate. Throws AuthRequestError — 409 legal hold, 403 wrong password,
 * 400 slug mismatch, 401 session lapsed.
 */
export async function eraseOrganization(
  orgId: string,
  reason: string,
  confirmSlug: string,
  password: string,
): Promise<ErasureCertificate> {
  const response = await authorizedFetch(`${API_URL}/platform/orgs/${orgId}/erase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason, confirm_slug: confirmSlug, password }),
  });
  return parseJsonOrThrow<ErasureCertificate>(response);
}

/**
 * Fetch the compliance export (metadata + audit trail) via GET .../compliance-export.
 *
 * Contract: returns the bundle for download. Throws AuthRequestError (404 unknown org,
 * 401 lapsed session).
 */
export async function exportCompliance(orgId: string): Promise<ComplianceExport> {
  const response = await authorizedFetch(
    `${API_URL}/platform/orgs/${orgId}/compliance-export`,
  );
  return parseJsonOrThrow<ComplianceExport>(response);
}
