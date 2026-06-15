/**
 * Role: Dev-only credential picker for locally configured demo accounts.
 * Used by: LoginPage.tsx.
 * Depends on: ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - No password is committed in this file.
 *   - The panel renders only accounts supplied via VITE_DEV_* env variables.
 */
import type { AuthScope } from "./types";

interface DemoAccount {
  label: string;
  email: string;
  password: string;
  scope: AuthScope;
}

interface EnvAccount {
  label: string;
  email: string | undefined;
  password: string | undefined;
  scope: AuthScope;
}

function configuredAccounts(): DemoAccount[] {
  const accounts: EnvAccount[] = [
    {
      label: "Platform admin",
      email: import.meta.env.VITE_DEV_PLATFORM_EMAIL,
      password: import.meta.env.VITE_DEV_PLATFORM_PASSWORD,
      scope: "platform",
    },
    {
      label: "Demo admin",
      email: import.meta.env.VITE_DEV_DEMO_ADMIN_EMAIL,
      password: import.meta.env.VITE_DEV_DEMO_ADMIN_PASSWORD,
      scope: "company",
    },
    {
      label: "Demo member",
      email: import.meta.env.VITE_DEV_DEMO_MEMBER_EMAIL,
      password: import.meta.env.VITE_DEV_DEMO_MEMBER_PASSWORD,
      scope: "company",
    },
    {
      label: "Globex admin",
      email: import.meta.env.VITE_DEV_GLOBEX_ADMIN_EMAIL,
      password: import.meta.env.VITE_DEV_GLOBEX_ADMIN_PASSWORD,
      scope: "company",
    },
    {
      label: "Globex member",
      email: import.meta.env.VITE_DEV_GLOBEX_MEMBER_EMAIL,
      password: import.meta.env.VITE_DEV_GLOBEX_MEMBER_PASSWORD,
      scope: "company",
    },
  ];

  return accounts.flatMap((account) => {
    const email = account.email?.trim();
    const password = account.password ?? "";
    if (email === undefined || email === "" || password === "") {
      return [];
    }
    return [{ label: account.label, email, password, scope: account.scope }];
  });
}

export function DevCredentialsPanel({
  onFill,
}: {
  onFill: (email: string, password: string, scope: AuthScope) => void;
}): React.JSX.Element | null {
  const accounts = configuredAccounts();
  if (accounts.length === 0) {
    return null;
  }

  return (
    <section className="mt-6 animate-fade-in rounded-xl border border-white/50 bg-white/65 p-4 shadow-sm backdrop-blur-xl">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-brand-purple">
        Dev test accounts
      </h2>
      <ul className="mt-3 space-y-2">
        {accounts.map((account) => (
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
