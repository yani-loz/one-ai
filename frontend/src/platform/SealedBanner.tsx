/**
 * Role: The "content is sealed" trust affordance — a quiet glass strip stating that the
 *       platform console shows operational metadata only and never tenant content. This
 *       is One AI's data-minimisation / processor-blindness story rendered into the UI.
 * Used by: PlatformConsolePage.tsx (and later the company detail screen).
 * Depends on: the aurora Tailwind theme only.
 * Key invariants: purely presentational; it makes a guarantee the architecture must keep
 *   (the /platform domain is metadata-only) — never weaken the copy to imply otherwise.
 */

/** A small lock glyph (inline SVG so it needs no icon dependency). */
function LockGlyph(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4 shrink-0 text-brand-teal"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="4" y="11" width="16" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

/**
 * Render the sealed-boundary banner.
 *
 * Contract: static, decorative-but-meaningful; no props. Sits at the foot of the console
 * to keep the guarantee in view without competing with the company list.
 */
export function SealedBanner(): React.JSX.Element {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-white/50 bg-white/40 px-4 py-3 text-sm backdrop-blur-xl">
      <LockGlyph />
      <p className="text-text-secondary">
        <span className="font-medium text-text-primary">Operational metadata only.</span> Company
        content is sealed — the platform admin can manage tenants but never read their data.
      </p>
    </div>
  );
}
