/**
 * Role: Thin HTTP client for break-glass support access — the platform side (request /
 *       list-mine / revoke) and the company side (inbox / approve / deny / revoke), via the
 *       shared authorizedFetch (which attaches the current session's access token).
 * Used by: SupportAccessPanel.tsx (platform), SupportInbox.tsx (company).
 * Depends on: ../identity (authorizedFetch, AuthRequestError), ./types,
 *             import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - authorizedFetch attaches the in-memory access token of the CURRENT session, so the
 *     platform calls run with the platform token and the company calls with the company
 *     token — the same function, domain-appropriate by who is signed in.
 *   - Every response is metadata only (a SupportGrant), never tenant content.
 */
import { authorizedFetch, AuthRequestError } from "../identity";
import type { SupportGrant } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

// — Platform side —

/** Request break-glass access to one company via POST /platform/orgs/{id}/support-requests. */
export async function requestSupportAccess(
  orgId: string,
  reason: string,
): Promise<SupportGrant> {
  const response = await authorizedFetch(
    `${API_URL}/platform/orgs/${orgId}/support-requests`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
  return parseJsonOrThrow<SupportGrant>(response);
}

/** List the calling platform admin's own requests via GET /platform/support-requests. */
export async function listMySupportRequests(): Promise<SupportGrant[]> {
  const response = await authorizedFetch(`${API_URL}/platform/support-requests`);
  return parseJsonOrThrow<SupportGrant[]>(response);
}

/** Revoke one of the admin's own requests via POST /platform/support-requests/{id}/revoke. */
export async function revokeMySupportRequest(grantId: string): Promise<SupportGrant> {
  const response = await authorizedFetch(
    `${API_URL}/platform/support-requests/${grantId}/revoke`,
    { method: "POST" },
  );
  return parseJsonOrThrow<SupportGrant>(response);
}

// — Company side (the approval inbox) —

/** List support requests targeting the caller's org via GET /support-access. */
export async function listSupportInbox(): Promise<SupportGrant[]> {
  const response = await authorizedFetch(`${API_URL}/support-access`);
  return parseJsonOrThrow<SupportGrant[]>(response);
}

/** Approve a request via POST /support-access/{id}/approve (opens the time box). */
export async function approveSupportRequest(grantId: string): Promise<SupportGrant> {
  const response = await authorizedFetch(`${API_URL}/support-access/${grantId}/approve`, {
    method: "POST",
  });
  return parseJsonOrThrow<SupportGrant>(response);
}

/** Deny a request via POST /support-access/{id}/deny. */
export async function denySupportRequest(grantId: string): Promise<SupportGrant> {
  const response = await authorizedFetch(`${API_URL}/support-access/${grantId}/deny`, {
    method: "POST",
  });
  return parseJsonOrThrow<SupportGrant>(response);
}

/** Revoke an approved grant via POST /support-access/{id}/revoke (cuts off access). */
export async function revokeSupportRequest(grantId: string): Promise<SupportGrant> {
  const response = await authorizedFetch(`${API_URL}/support-access/${grantId}/revoke`, {
    method: "POST",
  });
  return parseJsonOrThrow<SupportGrant>(response);
}
