/**
 * Role: One company rendered as a glass row — its unique apex crest (seeded from the
 *       org id), name, slug, lifecycle status, and seat count. The console's list is a
 *       gallery of these ("One Company. One AI." — each tenant a distinct emblem).
 * Used by: PlatformConsolePage.tsx.
 * Depends on: ../components/BrandMark, ../components/insignia/generateInsignia
 *             (seedFromString), ./StatusBadge, ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - The crest seed is derived from the immutable org id, so a company always renders
 *     the same emblem (a stable visual identity), independent of its name/slug.
 *   - Renders metadata only — no tenant content ever reaches this component.
 */
import { BrandMark } from "../components/BrandMark";
import { seedFromString } from "../components/insignia/generateInsignia";
import { StatusBadge } from "./StatusBadge";
import type { OrganizationSummary } from "./types";

/** Format an ISO timestamp as a short, locale-aware date (e.g. "1 Jun 2026"). */
function formatCreatedDate(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/**
 * Render a single company row.
 *
 * Contract: `company` is operational metadata; the row is presentational (no actions in
 * PR-1 — the detail screen + lifecycle actions arrive in PR-3). Enters with fade-in.
 */
export function CompanyCard({ company }: { company: OrganizationSummary }): React.JSX.Element {
  const seatLabel = company.user_count === 1 ? "1 seat" : `${company.user_count} seats`;

  return (
    <div className="flex animate-fade-in items-center gap-4 rounded-xl border border-white/50 bg-white/65 p-4 shadow-sm backdrop-blur-xl transition-all duration-200 hover:border-brand-teal/40">
      <BrandMark seed={seedFromString(company.id)} size={48} assembleSeconds={0} className="" />

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text-primary">{company.name}</p>
        <p className="truncate text-xs text-text-muted">{company.slug}</p>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <StatusBadge status={company.status} />
        <span className="text-xs text-text-muted">
          {seatLabel} · {formatCreatedDate(company.created_at)}
        </span>
      </div>
    </div>
  );
}
