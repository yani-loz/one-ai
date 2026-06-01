/**
 * Role: Shared TypeScript contracts for break-glass support access — mirrors the backend
 *       SupportGrantResponse + the request payload. Used by both the platform request panel
 *       and the company approval inbox.
 * Used by: supportClient.ts, SupportAccessPanel.tsx (platform), SupportInbox.tsx (company).
 * Depends on: nothing (leaf module — pure types).
 * Key invariants:
 *   - Metadata only — a grant is a consent/lifecycle record, never tenant content.
 *   - `is_active` is the backend's LIVE computation (approved AND within the time box); the
 *     UI treats it as the source of truth for "access is currently open", not `status`.
 */

/** Lifecycle state of a support grant (mirrors the backend CHECK). */
export type SupportGrantStatus = "requested" | "approved" | "denied" | "revoked";

/** One break-glass support-access grant as the UI sees it (metadata only). */
export interface SupportGrant {
  id: string;
  org_id: string;
  requested_by_admin_id: string;
  /** Denormalized requester email — who is asking (informed consent). */
  requested_by_email: string | null;
  reason: string;
  /** Stored lifecycle state; a string (not the union) to stay forward-compatible. */
  status: string;
  /** Live: approved AND within the time box. The source of truth for "access open now". */
  is_active: boolean;
  decided_at: string | null;
  decided_by_email: string | null;
  expires_at: string | null;
  created_at: string;
}
