/**
 * Role: Slide-in drawer to add a user to the caller's organization. Holds the form (with
 *       client-side validation mirroring the backend bounds + a one-click strong-password
 *       generator), submits via adminClient, then swaps to a credential hand-off panel.
 * Used by: AdminConsolePage.tsx.
 * Depends on: motion (orchestrated slide), ../identity (AuthRequestError),
 *             ../components/useDialogA11y, ./adminClient, ./userValidation, ./generatePassword,
 *             ./CreateUserSuccess, ./types, the aurora Tailwind theme.
 * Key invariants:
 *   - Submit stays disabled until email + name + password satisfy the backend bounds
 *     (isCreateUserFormValid), so a 422 round-trip is avoided — the password BYTE cap is the
 *     subtle one (an accented password can exceed 72 bytes under 128 chars).
 *   - A 409 here means EMAIL ALREADY TAKEN (distinguished by call-site, not by parsing the
 *     server message, which the status-only client never exposes). 401/403 → onSessionExpired
 *     so the parent logs out; any other failure → a generic message (never a misleading 409).
 *   - On success the form is REPLACED by a one-time credential hand-off (email + password to
 *     copy and share). `onCreated` fires when that panel is dismissed, so the list refreshes
 *     after the admin has the credentials — admin-set passwords are an MVP shortcut tracked in
 *     FIX_BEFORE_PROD (a real invite / first-login reset replaces this).
 */
import { useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { AuthRequestError } from "../identity";
import { useDialogA11y } from "../components/useDialogA11y";
import { createCompanyUser } from "./adminClient";
import { CreateUserSuccess } from "./CreateUserSuccess";
import { generatePassword } from "./generatePassword";
import { isCreateUserFormValid } from "./userValidation";
import type { CompanyUser, CompanyUserRole } from "./types";

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
    </div>
  );
}

/**
 * Render the create-user drawer.
 *
 * Contract: controlled by `open`; `onClose` dismisses it. `onCreated` fires once, when the
 * post-create hand-off panel is dismissed, so the parent refreshes the list. `onSessionExpired`
 * fires on a 401/403 so the parent logs out and the route guard redirects.
 */
export function CreateUserDrawer({
  open,
  onClose,
  onCreated,
  onSessionExpired,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  onSessionExpired: () => void;
}): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<CompanyUserRole>("member");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<CompanyUser | null>(null);

  const reduceMotion = useReducedMotion();
  // handleClose is a hoisted function declaration below, so the hook can reference it here.
  const containerRef = useDialogA11y<HTMLElement>(open, handleClose);

  function resetForm(): void {
    setEmail("");
    setFullName("");
    setRole("member");
    setPassword("");
    setShowPassword(false);
    setErrorMessage(null);
    setSubmitting(false);
    setResult(null);
  }

  function handleClose(): void {
    // Closing AFTER a successful create still refreshes the list (a user was created), even if
    // the admin dismisses via backdrop / X rather than the hand-off panel's "Done".
    const created = result !== null;
    resetForm();
    if (created) onCreated();
    onClose();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const created = await createCompanyUser({
        email: email.trim().toLowerCase(),
        full_name: fullName.trim(),
        role,
        password,
      });
      setResult(created); // swap to the credential hand-off; password stays for the copy field
    } catch (error) {
      // A lapsed session (401) or a role downgraded mid-session (403) → let the parent log
      // out and the guard redirect (a company_admin never legitimately gets 403 here).
      if (error instanceof AuthRequestError && (error.status === 401 || error.status === 403)) {
        onSessionExpired();
        return;
      }
      // A 409 on THIS endpoint can only mean a duplicate email (the last-admin 409 is a
      // patch/delete concern) — so the copy is unambiguous without parsing the server message.
      const duplicate = error instanceof AuthRequestError && error.status === 409;
      setErrorMessage(
        duplicate
          ? "A user with this email already exists. Try a different address."
          : "Couldn't create the user — check the details, and that the API is running.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = isCreateUserFormValid(email, fullName, password) && !submitting;

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
            aria-label="Add a user"
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-white/50 bg-brand-bg/80 p-6 shadow-xl backdrop-blur-xl"
          >
            <div className="mb-6 flex items-center justify-between">
              <h2 className="text-h3 font-bold text-text-primary">
                {result === null ? "Add a user" : "User added"}
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
              <CreateUserSuccess user={result} plaintextPassword={password} onDone={handleClose} />
            ) : (
              <form className="space-y-4" onSubmit={handleSubmit} noValidate>
                <TextField
                  id="user-name"
                  label="Full name"
                  value={fullName}
                  onChange={setFullName}
                  autoComplete="off"
                  placeholder="Anna Schmidt"
                />
                <TextField
                  id="user-email"
                  label="Email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  autoComplete="off"
                  placeholder="anna@your-company.de"
                />
                <div>
                  <label
                    htmlFor="user-role"
                    className="mb-1 block text-sm font-medium text-text-secondary"
                  >
                    Role
                  </label>
                  <select
                    id="user-role"
                    value={role}
                    onChange={(event) => setRole(event.target.value as CompanyUserRole)}
                    className={INPUT_CLASS}
                  >
                    <option value="member">Member</option>
                    <option value="company_admin">Administrator</option>
                  </select>
                </div>

                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label
                      htmlFor="user-password"
                      className="block text-sm font-medium text-text-secondary"
                    >
                      Initial password
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        setPassword(generatePassword());
                        setShowPassword(true);
                      }}
                      className="text-xs font-medium text-brand-blue transition-colors duration-200 hover:text-brand-teal"
                    >
                      Generate
                    </button>
                  </div>
                  <div className="relative">
                    <input
                      id="user-password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      autoComplete="new-password"
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      className={`${INPUT_CLASS} pr-16`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((shown) => !shown)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      className="absolute inset-y-0 right-3 flex items-center text-xs font-medium text-text-muted transition-colors duration-200 hover:text-text-primary"
                    >
                      {showPassword ? "Hide" : "Show"}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-text-muted">
                    8–128 characters. You hand it to the user after creating — they should change
                    it on first sign-in.
                  </p>
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
                  disabled={!canSubmit}
                  className="relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-brand-teal to-brand-blue px-8 py-3 font-semibold text-white transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_10px_25px_-5px_rgba(13,148,136,0.3)] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 disabled:hover:shadow-none"
                >
                  {submitting && (
                    <span
                      aria-hidden="true"
                      className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                    />
                  )}
                  {submitting ? "Adding…" : "Add user"}
                </button>
              </form>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
