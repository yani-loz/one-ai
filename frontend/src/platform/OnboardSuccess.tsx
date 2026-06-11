/**
 * Role: Success state of the onboard flow — celebrates the new company (its crest born
 *       from particles) and hands off the seed admin credentials to copy once.
 * Used by: OnboardCompanyDrawer.tsx.
 * Depends on: react (useEffect, useRef, useState), ../components/BrandMark,
 *             ../components/insignia/generateInsignia (seedFromString), ./types, the aurora
 *             Tailwind theme.
 * Key invariants:
 *   - The plaintext password is held only transiently in the client (the operator just
 *     typed it); the backend stores a bcrypt hash. The copy is hand-off, not storage —
 *     the panel says so and the value never leaves component state.
 *   - "Copied" is only claimed after the clipboard write CONFIRMS. A missing Clipboard API
 *     (plain-HTTP LAN host) or a rejected write shows "Copy failed — select the text manually"
 *     instead — never a false success over a one-time credential. (CopyField is kept
 *     structurally in sync with admin/CreateUserSuccess.tsx.)
 *   - The drawer swaps this panel in WITHOUT remounting the dialog, so the shared focus trap
 *     (keyed on `open`) does not re-fire — this panel re-asserts focus to its heading on mount
 *     (same pattern as admin/CreateUserSuccess.tsx), restoring Escape/Tab handling exactly while
 *     credentials are on screen and announcing the result without auto-reading the password.
 *   - The crest seed matches CompanyCard's (seedFromString(org id)) so the company's
 *     identity is consistent the moment it appears in the list.
 */
import { useEffect, useRef, useState } from "react";

import { BrandMark } from "../components/BrandMark";
import { seedFromString } from "../components/insignia/generateInsignia";
import type { OnboardedCompany } from "./types";

/** A read-only credential row with a one-tap copy affordance — "Copied" only after a CONFIRMED
 *  clipboard write; a missing/denied clipboard shows a manual-copy fallback, never false success. */
function CopyField({ label, value }: { label: string; value: string }): React.JSX.Element {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  async function copyToClipboard(): Promise<void> {
    // navigator.clipboard is absent off secure contexts (plain-HTTP LAN hosts) and in some
    // test/headless contexts; a present clipboard can still reject (permission denied). Only a
    // resolved write may claim "Copied" — this is a one-time credential, a false claim loses it.
    const clipboard = navigator.clipboard;
    if (clipboard === undefined) {
      setCopyState("failed");
      return;
    }
    try {
      await clipboard.writeText(value);
      setCopyState("copied");
      window.setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setCopyState("failed");
    }
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
          onClick={() => void copyToClipboard()}
          className="shrink-0 rounded-lg border border-white/50 bg-white/50 px-3 py-2 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] hover:border-brand-teal/50 active:scale-[0.97]"
        >
          {copyState === "copied" ? "Copied" : "Copy"}
        </button>
      </div>
      {copyState === "failed" && (
        <p role="alert" className="mt-1 animate-fade-in text-xs text-brand-red">
          Copy failed — select the text manually.
        </p>
      )}
    </div>
  );
}

/**
 * Render the onboard success / credential hand-off.
 *
 * Contract: shows the new company's crest (birthing) and its seed admin's email +
 * password for one-time hand-off. On mount it moves focus to the heading — restoring the
 * dialog focus context the form→success swap dropped (Tab/Escape work again) and announcing
 * the result — without placing the credentials in a live region. `onDone` closes the drawer
 * and refreshes the list.
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
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    // The drawer swapped the form out for this panel while staying open, so the shared focus trap
    // (keyed on `open`, unchanged across the swap) did not re-run and focus fell to <body>. Pull
    // it back to the heading so Tab/Escape work again inside the dialog and a screen reader
    // announces the result (without auto-reading the password block).
    headingRef.current?.focus();
  }, []);

  return (
    <div className="animate-fade-in">
      <BrandMark
        seed={seedFromString(company.organization.id)}
        size={84}
        assembleSeconds={1.8}
        className="mx-auto"
      />

      <h2
        ref={headingRef}
        tabIndex={-1}
        className="mt-4 text-center text-h3 font-bold text-text-primary outline-none"
      >
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
