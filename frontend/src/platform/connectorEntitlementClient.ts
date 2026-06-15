/**
 * Role: Thin HTTP client for the Tier-1 connector ENTITLEMENT backend
 *       (/platform/orgs/{id}/connector-entitlements) — a platform admin lists and grants/revokes
 *       which connector types a company may use (the plan ceiling), via the shared authorizedFetch.
 * Used by: ConnectorEntitlementsPanel.tsx (on the org-detail screen).
 * Depends on: ../api/apiBase (getApiBaseUrl), ../identity (authorizedFetch, AuthRequestError),
 *             import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - The target company is the {orgId} PATH param (a platform admin acts across orgs) — never a
 *     JWT-derived org. Metadata only — entitlement carries no credential/content.
 *   - Revoking (enabled=false) hides the type from the org but PERSISTS policies/connections (re-
 *     exposed on re-grant — no surprise cascade); the UI reflects the returned row's enabled flag.
 */
import { getApiBaseUrl } from "../api/apiBase";
import { authorizedFetch, AuthRequestError } from "../identity";

const API_URL = getApiBaseUrl();

/** A company's entitlement to one connector type (the plan ceiling). Mirrors EntitlementResponse. */
export interface ConnectorEntitlement {
  org_id: string;
  connector_type: string;
  enabled: boolean;
  granted_at: string;
  revoked_at: string | null;
}

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/** List a company's connector-type entitlements via GET .../connector-entitlements. */
export async function listConnectorEntitlements(
  orgId: string,
): Promise<ConnectorEntitlement[]> {
  return parseJsonOrThrow<ConnectorEntitlement[]>(
    await authorizedFetch(`${API_URL}/platform/orgs/${orgId}/connector-entitlements`),
  );
}

/**
 * Grant (enabled=true) or revoke (enabled=false) a company's entitlement to a connector type via
 * PUT .../connector-entitlements.
 *
 * Contract: returns the updated entitlement row. Throws AuthRequestError — 404 unknown org, 401
 * lapsed session.
 */
export async function setConnectorEntitlement(
  orgId: string,
  connectorType: string,
  enabled: boolean,
): Promise<ConnectorEntitlement> {
  const response = await authorizedFetch(
    `${API_URL}/platform/orgs/${orgId}/connector-entitlements`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connector_type: connectorType, enabled }),
    },
  );
  return parseJsonOrThrow<ConnectorEntitlement>(response);
}
