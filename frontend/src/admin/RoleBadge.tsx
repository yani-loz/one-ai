/**
 * Role: Small pill showing a company user's role (administrator vs member), using the One
 *       AI status vocabulary — the company-admin analogue of platform's StatusBadge.
 * Used by: UsersTable.tsx.
 * Depends on: ./types (CompanyUserRole), the aurora Tailwind theme.
 * Key invariants: only the two company roles render a styled pill; an unexpected value
 *   degrades to a neutral fallback (never throws), mirroring StatusBadge's forward-compat.
 */
import type { CompanyUserRole } from "./types";

interface RoleStyle {
  label: string;
  dot: string;
  text: string;
}

/**
 * Role → presentation, aurora tokens only: an administrator is purple (an elevated /
 * privileged identity), a member is neutral teal-leaning. Unknown values fall back neutral.
 */
const ROLE_STYLES: Record<CompanyUserRole, RoleStyle> = {
  company_admin: { label: "Administrator", dot: "bg-brand-purple", text: "text-brand-purple" },
  member: { label: "Member", dot: "bg-brand-teal", text: "text-brand-teal" },
};

const FALLBACK_STYLE: RoleStyle = {
  label: "Unknown",
  dot: "bg-text-muted/60",
  text: "text-text-muted",
};

/**
 * Render a role pill.
 *
 * Contract: `role` is the raw backend role string; it is matched against the known company
 * roles and falls back to a neutral "Unknown" pill otherwise.
 */
export function RoleBadge({ role }: { role: string }): React.JSX.Element {
  const style = ROLE_STYLES[role as CompanyUserRole] ?? FALLBACK_STYLE;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/50 bg-white/50 px-2.5 py-0.5 text-xs font-medium">
      <span className={`inline-block h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
      <span className={style.text}>{style.label}</span>
    </span>
  );
}
