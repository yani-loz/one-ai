/**
 * Role: Small shared formatters for the Platform console, so the company list and the
 *       detail screen render values identically.
 * Used by: CompanyCard.tsx, OrganizationDetailPage.tsx.
 * Depends on: nothing (pure).
 * Key invariants: an unparseable timestamp renders "—" (never "Invalid Date").
 */

/** Format an ISO-8601 timestamp as a short, locale-aware date (e.g. "1 Jun 2026"). */
export function formatShortDate(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
