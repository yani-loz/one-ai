/**
 * Role: Success state of the onboard flow — celebrates the new company (its crest born
 *       from particles) and hands off the seed admin credentials to copy once.
 * Used by: OnboardCompanyDrawer.tsx.
 * Depends on: ../components/BrandMark, ../components/insignia/generateInsignia
 *             (seedFromString), ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - The plaintext password is held only transiently in the client (the operator just
 *     typed it); the backend stores a bcrypt hash. The copy is hand-off, not storage —
 *     the panel says so and the value never leaves component state.
 *   - The crest seed matches CompanyCard's (seedFromString(org id)) so the company's
 *     identity is consistent the moment it appears in the list.
 */
import { useState } from "react";

import { BrandMark } from "../components/BrandMark";
import { seedFromString } from "../components/insignia/generateInsignia";
import type { OnboardedCompany } from "./types";

/** A read-only credential row with a one-tap copy affordance. */
function CopyField({ label, value }: { label: string; value: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false);

  function copyToClipboard(): void {
    // navigator.clipboard is absent in some test/headless contexts — guard it.
    void navigator.clipboard?.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div>
      <p className="mb-1 text-xs font-medium text-text-secondary">{label}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-white/50 bg-white/60 px-3 py-2 text-sm text-text-primary">
          {value}
        </code>
        <button
          type="button"
          onClick={copyToClipboard}
          className="shrink-0 rounded-lg border border-white/50 bg-white/50 px-3 py-2 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] hover:border-brand-teal/50 active:scale-[0.97]"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

/**
 * Render the onboard success / credential hand-off.
 *
 * Contract: shows the new company's crest (birthing) and its seed admin's email +
 * password for one-time hand-off. `onDone` closes the drawer and refreshes the list.
 */
export function OnboardSuccess({
  company,
  plaintextPassword,
  onDone,
}: {
  company: OnboardedCompany;
  plaintextPassword: string;
  onDone: () => void;
}): React.JSX.Element {
  return (
    <div className="animate-fade-in">
      <BrandMark
        seed={seedFromString(company.organization.id)}
        size={84}
        assembleSeconds={1.8}
        className="mx-auto"
      />

      <h2 className="mt-4 text-center text-h3 font-bold text-text-primary">
        <span className="text-brand-gradient">{company.organization.name}</span> is live
      </h2>
      <p className="mt-1 text-center text-sm text-text-secondary">
        Hand these credentials to the company admin — shown once, not stored.
      </p>

      <div className="mt-6 space-y-4 rounded-xl border border-white/50 bg-white/40 p-4">
        <CopyField label="Admin email" value={company.admin.email} />
        <CopyField label="Temporary password" value={plaintextPassword} />
      </div>

      <button
        type="button"
        onClick={onDone}
        className="mt-6 inline-flex w-full items-center justify-center rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-8 py-3 font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98]"
      >
        Done
      </button>
    </div>
  );
}
