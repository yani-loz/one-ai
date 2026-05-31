/**
 * Role: Dev-only glass panel listing the three demo accounts as click-to-fill
 *       buttons. ISOLATED in its own file so deleting it before production is a
 *       single-file delete + one import removal in LoginPage.
 * Used by: LoginPage.tsx.
 * Depends on: ../identity/types (Role/AuthScope), the aurora Tailwind theme.
 * Key invariants: these are DEV credentials only — the header says so, and the whole
 *   file must be removed before production (see FIX_BEFORE_PROD.md).
 */
import type { AuthScope } from "./types";

interface DemoAccount {
  label: string;
  email: string;
  password: string;
  scope: AuthScope;
}

/** The seeded demo accounts — dev only, never ship to production. Two companies
 *  (`demo`, `globex`) let you exercise cross-tenant isolation in the UI. */
const DEMO_ACCOUNTS: readonly DemoAccount[] = [
  {
    label: "Platform admin",
    email: "super@ethera.ai",
    password: "Sup3r-Dev-Only-2026!",
    scope: "platform",
  },
  {
    label: "Demo admin",
    email: "admin@demo.oneai",
    password: "Adm1n-Dev-Only-2026!",
    scope: "company",
  },
  {
    label: "Demo member",
    email: "member@demo.oneai",
    password: "Memb3r-Dev-Only-2026!",
    scope: "company",
  },
  {
    label: "Globex admin",
    email: "admin@globex.oneai",
    password: "Adm1n-Dev-Only-2026!",
    scope: "company",
  },
  {
    label: "Globex member",
    email: "member@globex.oneai",
    password: "Memb3r-Dev-Only-2026!",
    scope: "company",
  },
];

/**
 * Render the dev credential picker.
 *
 * Contract: each row is a button that, on click, calls `onFill` with the account's
 * email/password/scope so the login form can prefill and switch endpoint. Purely
 * presentational beyond that callback.
 */
export function DevCredentialsPanel({
  onFill,
}: {
  onFill: (email: string, password: string, scope: AuthScope) => void;
}): React.JSX.Element {
  return (
    <section className="mt-6 animate-fade-in rounded-xl border border-white/50 bg-white/65 p-4 shadow-sm backdrop-blur-xl">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-brand-purple">
        Dev test accounts — remove before production
      </h2>
      <ul className="mt-3 space-y-2">
        {DEMO_ACCOUNTS.map((account) => (
          <li key={account.email}>
            <button
              type="button"
              onClick={() => onFill(account.email, account.password, account.scope)}
              className="flex w-full items-center justify-between rounded-lg border border-white/50 bg-white/50 px-3 py-2 text-left transition-all duration-200 hover:scale-[1.02] hover:border-brand-teal/50 active:scale-[0.98]"
            >
              <span className="text-sm font-medium text-text-primary">{account.label}</span>
              <span className="text-xs text-text-muted">{account.email}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
