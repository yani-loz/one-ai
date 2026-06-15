/**
 * Role: Thin HTTP client for the Tier-2 connector GOVERNANCE backend (/admin/connectors/governance|
 *       policies|overrides) — a company admin reads the §7 metadata-only health roll-up and sets the
 *       org-wide policy + per-user grant/deny overrides, via the shared authorizedFetch.
 * Used by: useConnectorGovernance (the ConnectorGovernancePanel controller).
 * Depends on: ../api/apiBase (getApiBaseUrl), ../identity (authorizedFetch, AuthRequestError),
 *             ./connectorGovernanceTypes, import.meta.env.VITE_API_URL.
 * Key invariants:
 *   - All calls go through authorizedFetch; org scope is derived server-side from the JWT (a cross-
 *     org target is a 404). Responses are METADATA ONLY (no secret/host/username/content).
 *   - Setting the org-wide policy or a grant override beyond the company's entitlement is rejected
 *     server-side (403) — the panel also disables those controls when entitled=false. Every write
 *     returns the FULL refreshed governance view so the panel re-renders from the server's truth.
 */
import { getApiBaseUrl } from "../api/apiBase";
import { authorizedFetch, AuthRequestError } from "../identity";
import type { ConnectorGovernance } from "./connectorGovernanceTypes";

const API_URL = getApiBaseUrl();

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AuthRequestError(response.status, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Read the governance view for a connector type (ceiling + org-wide policy + overrides + health). */
export async function getConnectorGovernance(
  connectorType: string,
): Promise<ConnectorGovernance> {
  return parseJsonOrThrow<ConnectorGovernance>(
    await authorizedFetch(`${API_URL}/admin/connectors/governance/${connectorType}`),
  );
}

/**
 * Set the org-wide enable/disable for a connector type via PUT /admin/connectors/policies.
 *
 * Contract: returns the refreshed governance view. Throws AuthRequestError — 403 when enabling a
 * type the company isn't entitled to, 401 when the session has lapsed.
 */
export async function setOrgWidePolicy(
  connectorType: string,
  orgWideEnabled: boolean,
): Promise<ConnectorGovernance> {
  const response = await authorizedFetch(`${API_URL}/admin/connectors/policies`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector_type: connectorType, org_wide_enabled: orgWideEnabled }),
  });
  return parseJsonOrThrow<ConnectorGovernance>(response);
}

/**
 * Grant or deny a connector type to one user via PUT /admin/connectors/overrides.
 *
 * Contract: returns the refreshed governance view. Throws AuthRequestError — 403 when granting
 * beyond entitlement, 401 when the session has lapsed.
 */
export async function setUserOverride(
  connectorType: string,
  userId: string,
  overrideType: "grant" | "deny",
): Promise<ConnectorGovernance> {
  const response = await authorizedFetch(`${API_URL}/admin/connectors/overrides`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      connector_type: connectorType,
      user_id: userId,
      override_type: overrideType,
    }),
  });
  return parseJsonOrThrow<ConnectorGovernance>(response);
}

/**
 * Clear a user's override via DELETE /admin/connectors/overrides/{type}/{userId} (reverts them to
 * the org-wide policy). Returns the refreshed governance view.
 */
export async function clearUserOverride(
  connectorType: string,
  userId: string,
): Promise<ConnectorGovernance> {
  return parseJsonOrThrow<ConnectorGovernance>(
    await authorizedFetch(
      `${API_URL}/admin/connectors/overrides/${connectorType}/${userId}`,
      { method: "DELETE" },
    ),
  );
}
