/**
 * Role: Shared TypeScript contracts for the Platform console module — mirrors the
 *       backend /platform/* API shapes (organization metadata + onboarding).
 * Used by: platformClient.ts, PlatformConsolePage.tsx, OnboardCompanyDrawer.tsx,
 *          OnboardSuccess.tsx, CompanyCard.tsx, StatusBadge.tsx, OrganizationDetailPage.tsx.
 * Depends on: nothing (leaf module — pure types).
 * Key invariants:
 *   - OrganizationSummary is METADATA ONLY (id/name/slug/status/user_count/created_at) —
 *     it mirrors the backend OrganizationResponse and never carries tenant content.
 *   - OnboardCompanyRequest field bounds mirror the backend Pydantic validators
 *     (SafeName, slug pattern, BcryptPassword) so client-side checks match the server.
 */

/**
 * Lifecycle states a company (tenant) can be in. The backend `status` column is still a
 * free string defaulting to "active" (PR-3 promotes it to a CHECK-constrained enum); the
 * UI treats any unknown value as a neutral fallback, so this stays forward-compatible.
 */
export type OrganizationStatus = "active" | "suspended" | "onboarding" | "offboarded";

/** A customer company as the platform console sees it — operational metadata only. */
export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  /** Lifecycle status; a string (not the enum) since the backend column is not yet constrained. */
  status: string;
  /** Active + inactive user count for the org (no tenant content). */
  user_count: number;
  /** ISO-8601 creation timestamp. */
  created_at: string;
}

/**
 * One company's lifecycle detail from GET /platform/orgs/{id} — metadata + legal hold.
 * Governance posture (region/residency/DPA/works-council/AI-Act/retention) arrives in
 * PC-03b. Still METADATA ONLY — never tenant content.
 */
export interface OrganizationDetail {
  id: string;
  name: string;
  slug: string;
  status: string;
  user_count: number;
  legal_hold: boolean;
  created_at: string;
}

/**
 * One entry in a company's append-only audit trail from GET /platform/orgs/{id}/audit —
 * metadata about an ACTION (who/what/when/where), never tenant content or secrets. Mirrors
 * the backend AuditLogEntryResponse.
 */
export interface AuditLogEntry {
  id: string;
  /** ISO-8601 timestamp the action occurred. */
  occurred_at: string;
  /** "platform_admin" | "user" | "system". */
  actor_type: string;
  actor_id: string | null;
  /** Denormalized so a deleted/renamed actor's past actions stay attributable. */
  actor_email: string | null;
  /** Dotted action namespace, e.g. "org.suspend", "auth.login.success". */
  action: string;
  org_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  /** Structured, never-secret metadata about the action. */
  details: Record<string, unknown>;
  ip_address: string | null;
  request_id: string | null;
}

/**
 * Payload to onboard a new company plus its first company_admin. Field bounds mirror the
 * backend OrganizationCreateRequest validators:
 *   - org_name / admin_full_name: 1..200 chars, no control characters (SafeName).
 *   - org_slug: 1..100 chars matching ^[a-z0-9][a-z0-9-]*$.
 *   - admin_password: 8..128 chars (and <= 72 UTF-8 bytes server-side, bcrypt's limit).
 */
export interface OnboardCompanyRequest {
  org_name: string;
  org_slug: string;
  admin_email: string;
  admin_full_name: string;
  admin_password: string;
}

/** The newly-created company_admin as returned by onboarding (never includes a hash). */
export interface OnboardedAdmin {
  id: string;
  email: string;
  full_name: string;
  role: string;
  org_id: string;
  created_at: string;
}

/** Result of a successful onboard: the new org metadata + its seed admin. */
export interface OnboardedCompany {
  organization: OrganizationSummary;
  admin: OnboardedAdmin;
}
