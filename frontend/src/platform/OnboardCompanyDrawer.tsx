/**
 * Role: Slide-in drawer to onboard a new company + its first company_admin. Holds the
 *       form (with client-side validation mirroring the backend bounds), submits via
 *       platformClient, and swaps to the success/credential hand-off on completion.
 * Used by: PlatformConsolePage.tsx.
 * Depends on: motion (orchestrated slide), ../identity (AuthRequestError), ./platformClient,
 *             ./OnboardSuccess, ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - The slug auto-suggests from the name until the operator edits it, then stops
 *     overriding their input.
 *   - A 409 (duplicate slug / admin email) maps to a specific message; any other failure
 *     maps to a generic connectivity message (never a misleading "already exists").
 *   - Closing after a successful onboard still refreshes the list (a company was created).
 */
import { useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { AuthRequestError } from "../identity";
import { isOnboardFormValid, slugify } from "./onboardValidation";
import { onboardCompany } from "./platformClient";
import { OnboardSuccess } from "./OnboardSuccess";
import { useDialogA11y } from "../components/useDialogA11y";
import type { OnboardedCompany } from "./types";

const SLIDE = { duration: 0.5, ease: [0.32, 0.72, 0, 1] } as const;
const INPUT_CLASS =
  "w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-4 py-3 text-text-primary outline-none transition-[border-color,box-shadow] duration-200 placeholder:text-text-muted focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)]";

/** A labelled text input matching the One AI form style. */
function TextField(props: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  autoComplete?: string;
  hint?: string;
}): React.JSX.Element {
  return (
    <div>
      <label htmlFor={props.id} className="mb-1 block text-sm font-medium text-text-secondary">
        {props.label}
      </label>
      <input
        id={props.id}
        type={props.type ?? "text"}
        value={props.value}
        autoComplete={props.autoComplete}
        onChange={(event) => props.onChange(event.target.value)}
        required
        className={INPUT_CLASS}
        placeholder={props.placeholder}
      />
      {props.hint !== undefined && <p className="mt-1 text-xs text-text-muted">{props.hint}</p>}
    </div>
  );
}

/**
 * Render the onboard drawer.
 *
 * Contract: controlled by `open`; `onClose` dismisses it, `onOnboarded` fires once after
 * a successful onboard so the parent can refresh the list, and `onSessionExpired` fires
 * when the platform session has lapsed (a 401 during submit) so the parent can log out
 * and let the route guard redirect — matching how the company list handles a 401.
 */
export function OnboardCompanyDrawer({
  open,
  onClose,
  onOnboarded,
  onSessionExpired,
}: {
  open: boolean;
  onClose: () => void;
  onOnboarded: () => void;
  onSessionExpired: () => void;
}): React.JSX.Element {
  const [orgName, setOrgName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [adminName, setAdminName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<OnboardedCompany | null>(null);

  const reduceMotion = useReducedMotion();
  // handleClose is a hoisted function declaration below, so the hook can reference it here.
  const containerRef = useDialogA11y<HTMLElement>(open, handleClose);

  function resetForm(): void {
    setOrgName("");
    setOrgSlug("");
    setSlugEdited(false);
    setAdminName("");
    setAdminEmail("");
    setAdminPassword("");
    setErrorMessage(null);
    setResult(null);
    setSubmitting(false);
  }

  function handleNameChange(value: string): void {
    setOrgName(value);
    if (!slugEdited) setOrgSlug(slugify(value));
  }

  function handleClose(): void {
    const wasOnboarded = result !== null;
    resetForm();
    if (wasOnboarded) onOnboarded();
    onClose();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const onboarded = await onboardCompany({
        org_name: orgName.trim(),
        org_slug: orgSlug,
        admin_full_name: adminName.trim(),
        admin_email: adminEmail.trim().toLowerCase(),
        admin_password: adminPassword,
      });
      setResult(onboarded);
    } catch (error) {
      // A lapsed platform session (401) is handled like the list does — log out and let
      // the route guard redirect, rather than showing a misleading connectivity message.
      if (error instanceof AuthRequestError && error.status === 401) {
        onSessionExpired();
        return;
      }
      const duplicate = error instanceof AuthRequestError && error.status === 409;
      setErrorMessage(
        duplicate
          ? "That slug or admin email is already taken. Try a different one."
          : "Couldn't complete onboarding — check the details, and that the API is running.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    isOnboardFormValid(orgName, orgSlug, adminName, adminEmail, adminPassword) && !submitting;

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={handleClose}
            className="fixed inset-0 z-40 bg-text-primary/20 backdrop-blur-sm"
            aria-hidden="true"
          />
          <motion.aside
            ref={containerRef}
            initial={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            animate={reduceMotion ? { opacity: 1 } : { x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            transition={reduceMotion ? { duration: 0 } : SLIDE}
            role="dialog"
            aria-modal="true"
            aria-label="Onboard a company"
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-white/50 bg-brand-bg/80 p-6 shadow-xl backdrop-blur-xl"
          >
            <div className="mb-6 flex items-center justify-between">
              <h2 className="text-h3 font-bold text-text-primary">
                {result === null ? "Onboard a company" : "Company onboarded"}
              </h2>
              <button
                type="button"
                onClick={handleClose}
                aria-label="Close"
                className="rounded-lg px-2 py-1 text-text-muted transition-colors duration-200 hover:text-brand-red"
              >
                ✕
              </button>
            </div>

            {result !== null ? (
              <OnboardSuccess
                company={result}
                plaintextPassword={adminPassword}
                onDone={handleClose}
              />
            ) : (
              <form className="space-y-4" onSubmit={handleSubmit} noValidate>
                <TextField
                  id="org-name"
                  label="Company name"
                  value={orgName}
                  onChange={handleNameChange}
                  placeholder="Müller Maschinenbau GmbH"
                />
                <TextField
                  id="org-slug"
                  label="Slug"
                  value={orgSlug}
                  onChange={(value) => {
                    setSlugEdited(true);
                    setOrgSlug(value);
                  }}
                  placeholder="mueller-maschinenbau"
                  hint="Lowercase letters, numbers and dashes. Used in URLs — cannot change later."
                />
                <TextField
                  id="admin-name"
                  label="Admin full name"
                  value={adminName}
                  onChange={setAdminName}
                  autoComplete="off"
                  placeholder="Anna Schmidt"
                />
                <TextField
                  id="admin-email"
                  label="Admin email"
                  type="email"
                  value={adminEmail}
                  onChange={setAdminEmail}
                  autoComplete="off"
                  placeholder="anna@mueller-maschinenbau.de"
                />
                <TextField
                  id="admin-password"
                  label="Temporary password"
                  type="password"
                  value={adminPassword}
                  onChange={setAdminPassword}
                  autoComplete="new-password"
                  hint="At least 8 characters. The admin should change it on first sign-in."
                />

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
                  disabled={!canSubmit}
                  className="relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-8 py-3 font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 disabled:hover:shadow-none"
                >
                  {submitting && (
                    <span
                      aria-hidden="true"
                      className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                    />
                  )}
                  {submitting ? "Onboarding…" : "Onboard company"}
                </button>
              </form>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
