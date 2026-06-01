/**
 * Role: Small status pill for a company's lifecycle state — a coloured dot + label
 *       following the One AI status vocabulary (teal healthy, muted inactive, etc.).
 * Used by: CompanyCard.tsx (and later the company detail screen).
 * Depends on: ./types (OrganizationStatus), the aurora Tailwind theme.
 * Key invariants: an unrecognised status renders the neutral fallback (never throws), so
 *   a future backend status value degrades gracefully instead of crashing the list.
 */
import type { OrganizationStatus } from "./types";

interface StatusStyle {
  label: string;
  /** Dot colour + optional ambient animation (per the design language status vocabulary). */
  dot: string;
  text: string;
}

/**
 * Status → presentation, using ONLY aurora-palette tokens (no raw Tailwind colors):
 * `active` breathes teal; `onboarding` breathes purple (AI-action); `suspended` is
 * brand-red (a halted/attention state); terminal/unknown states are muted-neutral.
 */
const STATUS_STYLES: Record<OrganizationStatus, StatusStyle> = {
  active: { label: "Active", dot: "bg-brand-teal animate-pulse-dot", text: "text-brand-teal" },
  onboarding: {
    label: "Onboarding",
    dot: "bg-brand-purple animate-pulse-dot",
    text: "text-brand-purple",
  },
  suspended: { label: "Suspended", dot: "bg-brand-red", text: "text-brand-red" },
  offboarded: { label: "Offboarded", dot: "bg-text-muted/60", text: "text-text-muted" },
};

const FALLBACK_STYLE: StatusStyle = {
  label: "Unknown",
  dot: "bg-text-muted/60",
  text: "text-text-muted",
};

/**
 * Render a company status pill.
 *
 * Contract: `status` is the raw backend string; it is matched case-insensitively against
 * the known lifecycle states and falls back to a neutral "Unknown" pill otherwise.
 */
export function StatusBadge({ status }: { status: string }): React.JSX.Element {
  const style = STATUS_STYLES[status.toLowerCase() as OrganizationStatus] ?? FALLBACK_STYLE;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/50 bg-white/50 px-2.5 py-0.5 text-xs font-medium">
      <span className={`inline-block h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
      <span className={style.text}>{style.label}</span>
    </span>
  );
}
