/**
 * Role: Aurora glass login screen — email/password form with a subtle platform-admin
 *       toggle that switches the auth endpoint, plus the dev credentials panel.
 * Used by: App.tsx (the public /login route).
 * Depends on: ./useAuth, ./DevCredentialsPanel, ./types, react-router-dom (useNavigate),
 *             motion (entrance), the aurora Tailwind theme.
 * Key invariants:
 *   - On success the page navigates to "/" (replace) — already-authenticated users
 *     are redirected away by the effect so /login is never a dead end.
 *   - A 401 surfaces a GENERIC "Invalid email or password." (no user enumeration,
 *     regardless of which field was wrong). A non-401 (network down, server reloading,
 *     5xx) surfaces a DISTINCT "couldn't reach the server" message, so a transient
 *     outage is never mistaken for bad credentials.
 */
import { useEffect, useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { useNavigate } from "react-router-dom";

import { AuthRequestError } from "./authClient";
import { DevCredentialsPanel } from "./DevCredentialsPanel";
import { useAuth } from "./useAuth";
import type { AuthScope } from "./types";

export function LoginPage(): React.JSX.Element {
  const { status, login, platformLogin } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [scope, setScope] = useState<AuthScope>("company");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Already-authenticated users (e.g. after a bootstrap) skip the login form.
  useEffect(() => {
    if (status === "authenticated") {
      navigate("/", { replace: true });
    }
  }, [status, navigate]);

  function fillFromDevAccount(devEmail: string, devPassword: string, devScope: AuthScope): void {
    setEmail(devEmail);
    setPassword(devPassword);
    setScope(devScope);
    setErrorMessage(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    try {
      if (scope === "platform") {
        await platformLogin(email, password);
      } else {
        await login(email, password);
      }
      navigate("/", { replace: true });
    } catch (error) {
      // 401 = bad credentials → generic message (never reveal which field was wrong).
      // Anything else (network down, API reloading, 5xx) is NOT a credential error, so
      // say that instead of misleading the user with "wrong password".
      const badCredentials = error instanceof AuthRequestError && error.status === 401;
      setErrorMessage(
        badCredentials
          ? "Invalid email or password."
          : "Couldn't reach the server. Check that the API is running, then try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const isPlatform = scope === "platform";

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="flex min-h-screen items-center justify-center p-6"
    >
      <div className="w-full max-w-md">
        <section className="rounded-xl border border-white/50 bg-white/65 p-8 shadow-sm backdrop-blur-xl">
          <div className="relative mx-auto mb-6 h-16 w-16">
            <div className="absolute inset-0 animate-aura-pulse rounded-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-purple blur-md" />
            <div className="absolute inset-2 rounded-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-purple" />
          </div>

          <h1 className="text-center text-h2 font-bold text-brand-gradient">One AI</h1>
          <p className="mt-1 text-center text-sm text-text-secondary">
            {isPlatform ? "Platform administration" : "Sign in to your workspace"}
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit} noValidate>
            <div>
              <label htmlFor="email" className="mb-1 block text-sm font-medium text-text-secondary">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                className="w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-4 py-3 text-text-primary outline-none transition-[border-color,box-shadow] duration-200 placeholder:text-text-muted focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)]"
                placeholder="you@company.com"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="mb-1 block text-sm font-medium text-text-secondary"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                className="w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-4 py-3 text-text-primary outline-none transition-[border-color,box-shadow] duration-200 placeholder:text-text-muted focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)]"
                placeholder="••••••••"
              />
            </div>

            {errorMessage !== null && (
              <p
                role="alert"
                className="animate-fade-in rounded-lg border border-brand-red/30 bg-brand-red/10 px-3 py-2 text-sm text-brand-red"
              >
                {errorMessage}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-8 py-3 font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 disabled:hover:shadow-none"
            >
              {submitting && (
                <span
                  aria-hidden="true"
                  className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                />
              )}
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <button
            type="button"
            onClick={() => {
              setScope(isPlatform ? "company" : "platform");
              setErrorMessage(null);
            }}
            className="mt-4 block w-full text-center text-xs text-text-muted transition-colors duration-200 hover:text-brand-purple"
          >
            {isPlatform ? "← Back to company sign in" : "Sign in as platform admin"}
          </button>
        </section>

        <DevCredentialsPanel onFill={fillFromDevAccount} />
      </div>
    </motion.main>
  );
}
