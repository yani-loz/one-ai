/**
 * Role: One company rendered as a glass row that links to its detail screen — its unique
 *       apex crest (seeded from the org id), name, slug, lifecycle status, and seat count.
 *       The console's list is a gallery of these ("One Company. One AI.").
 * Used by: PlatformConsolePage.tsx.
 * Depends on: react-router-dom (Link), ../components/BrandMark,
 *             ../components/insignia/generateInsignia (seedFromString), ./StatusBadge,
 *             ./format, ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - The crest seed is derived from the immutable org id, so a company always renders
 *     the same emblem (a stable visual identity), independent of its name/slug.
 *   - Renders metadata only — no tenant content ever reaches this component.
 *   - The whole row is a Link to /platform/orgs/{id} (keyboard-accessible navigation).
 */
import { Link } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";
import { seedFromString } from "../components/insignia/generateInsignia";
import { formatShortDate } from "./format";
import { StatusBadge } from "./StatusBadge";
import type { OrganizationSummary } from "./types";

/**
 * Render a single company row as a link to its detail screen.
 *
 * Contract: `company` is operational metadata; clicking (or Enter on) the row navigates
 * to /platform/orgs/{id}. Enters with fade-in.
 */
export function CompanyCard({ company }: { company: OrganizationSummary }): React.JSX.Element {
  const seatLabel = company.user_count === 1 ? "1 seat" : `${company.user_count} seats`;

  return (
    <Link
      to={`/platform/orgs/${company.id}`}
      className="flex animate-fade-in items-center gap-4 rounded-xl border border-white/50 bg-white/65 p-4 shadow-sm backdrop-blur-xl transition-all duration-200 hover:scale-[1.01] hover:border-brand-teal/40"
    >
      <BrandMark seed={seedFromString(company.id)} size={48} assembleSeconds={0} className="" />

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-text-primary">{company.name}</p>
        <p className="truncate text-xs text-text-muted">{company.slug}</p>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <StatusBadge status={company.status} />
        <span className="text-xs text-text-muted">
          {seatLabel} · {formatShortDate(company.created_at)}
        </span>
      </div>
    </Link>
  );
}
