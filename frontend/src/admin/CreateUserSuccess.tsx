/**
 * Role: Success / credential hand-off shown after a company admin adds a user — reveals the
 *       new user's email + initial password to copy ONCE and share securely.
 * Used by: CreateUserDrawer.tsx.
 * Depends on: react (useState), ../components/AgentInsignia + insignia/generateInsignia
 *             (the new user's personal-AI mark), ./types (CompanyUser), the aurora theme.
 * Key invariants:
 *   - The plaintext password is held only transiently in client state (the admin just set or
 *     generated it); the backend stores a bcrypt hash. The copy is hand-off, NOT storage.
 *   - This hand-off is the deliberate MVP shortcut until an email-invite / first-login reset
 *     lands (FIX_BEFORE_PROD: admin-set passwords) — so the panel tells the admin to share it
 *     securely and have the user change it on first sign-in.
 */
import { useState } from "react";

import { AgentInsignia } from "../components/AgentInsignia";
import { seedFromString } from "../components/insignia/generateInsignia";
import type { CompanyUser } from "./types";

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
 * Render the create-user success / credential hand-off.
 *
 * Contract: shows the new user's personal-AI mark + their email and initial password for a
 * one-time hand-off. `onDone` closes the drawer and refreshes the list.
 */
export function CreateUserSuccess({
  user,
  plaintextPassword,
  onDone,
}: {
  user: CompanyUser;
  plaintextPassword: string;
  onDone: () => void;
}): React.JSX.Element {
  return (
    <div className="animate-fade-in">
      <AgentInsignia
        seed={seedFromString(user.id)}
        branch="orchestrator"
        rank={user.role === "company_admin" ? 2 : 1}
        size={72}
        assembleSeconds={1.6}
        label={`${user.full_name}'s personal AI`}
        className="mx-auto"
      />

      <h2 className="mt-4 text-center text-h3 font-bold text-text-primary">
        <span className="text-brand-gradient">{user.full_name}</span> added
      </h2>
      <p className="mt-1 text-center text-sm text-text-secondary">
        Share these credentials securely — shown once, not stored. The user should change the
        password on first sign-in.
      </p>

      <div className="mt-6 space-y-4 rounded-xl border border-white/50 bg-white/40 p-4">
        <CopyField label="Email" value={user.email} />
        <CopyField label="Initial password" value={plaintextPassword} />
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
