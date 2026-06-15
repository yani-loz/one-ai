/**
 * Role: The HITL self-connect modal (CO-01 Tier 3) — an explicit GDPR Art. 7 CONSENT step, then the
 *       mailbox form (email auto-detects the IMAP server, app-password, advanced host/port). On
 *       submit it POSTs /me/connectors WITH the consent block, then live-tests the new connection.
 * Used by: ConnectorDetailPage.tsx (the "Connect my mailbox" action on the Connection tab).
 * Depends on: react, motion, ../identity (AuthRequestError), ../components/useDialogA11y,
 *             ./meConnectorClient (selfConnect + testMyConnection), ./imapProviders, ./types.
 * Key invariants:
 *   - The connect button stays disabled until the consent checkbox is ticked AND the form is valid —
 *     no connection is ever created without recorded consent (the backend also 400s a missing one).
 *   - Submit creates THEN tests (the backend tests a stored connection — no pre-save test), so the
 *     result panel shows the real verification. The mailbox is saved either way; a failed sign-in is
 *     surfaced and the parent refreshes the list to show it.
 *   - 403 = the user isn't allowed this type (policy) → shown inline; 401 → onSessionExpired (logout);
 *     409 = already connected; 503 = server connector key mis-set. The password is write-only.
 *   - Host/port auto-fill from the email's domain UNTIL the user edits the server fields by hand.
 */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { AuthRequestError } from "../identity";
import { useDialogA11y } from "../components/useDialogA11y";
import { selfConnect, testMyConnection } from "./meConnectorClient";
import { appPasswordHelpUrl, detectImapSettings } from "./imapProviders";
import type { Connection } from "./types";

/** The consent scope + version recorded with every self-connect (mirrors the backend defaults). */
const CONSENT_SCOPE = "mailbox:read";
const CONSENT_VERSION = "v1";

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
        autoComplete="off"
        onChange={(event) => props.onChange(event.target.value)}
        className={INPUT_CLASS}
        placeholder={props.placeholder}
      />
    </div>
  );
}

/** Map a self-connect failure status to a user-facing message (never the raw server text). */
function messageFor(status: number): string {
  if (status === 403) return "Your administrator hasn't enabled this connector for you.";
  if (status === 400) return "Please accept the consent above to continue.";
  if (status === 409) return "You've already connected this mailbox.";
  if (status === 503) return "The server isn't configured to store credentials. Contact support.";
  return "Couldn't connect — check the details, and that the API is running.";
}

/**
 * Render the consent-gated self-connect modal.
 *
 * Contract: controlled by `open`; `onClose` dismisses. `onConnected` fires when a connection was
 * created (refresh the list). `onSessionExpired` fires on a 401.
 */
export function ConsentModal({
  open,
  connectorLabel,
  onClose,
  onConnected,
  onSessionExpired,
}: {
  open: boolean;
  connectorLabel: string;
  onClose: () => void;
  onConnected: () => void;
  onSessionExpired: () => void;
}): React.JSX.Element {
  const [consented, setConsented] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [host, setHost] = useState("");
  const [port, setPort] = useState(993);
  const [serverEdited, setServerEdited] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<Connection | null>(null);

  const reduceMotion = useReducedMotion();
  const containerRef = useDialogA11y<HTMLElement>(open, handleClose);
  const connectionCreatedRef = useRef(false);

  useEffect(() => {
    if (open) return;
    // Parent force-closed (e.g. logout) — reset for the next open.
    resetForm();
  }, [open]);

  function resetForm(): void {
    connectionCreatedRef.current = false;
    setConsented(false);
    setEmail("");
    setDisplayName("");
    setPassword("");
    setShowPassword(false);
    setHost("");
    setPort(993);
    setServerEdited(false);
    setShowAdvanced(false);
    setSubmitting(false);
    setErrorMessage(null);
    setResult(null);
  }

  function handleClose(): void {
    if (submitting) return; // block close mid-flight (a connection may exist server-side)
    const created = result !== null || connectionCreatedRef.current;
    resetForm();
    if (created) onConnected();
    onClose();
  }

  /** Update the email and (until the user edits the server fields) auto-fill host/port from it. */
  function handleEmailChange(value: string): void {
    setEmail(value);
    if (serverEdited) return;
    const detected = detectImapSettings(value.trim());
    if (detected !== null) {
      setHost(detected.host);
      setPort(detected.port);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    const username = email.trim().toLowerCase();
    try {
      const created = await selfConnect({
        connector_type: "imap",
        display_name: displayName.trim() || username,
        host: host.trim(),
        port,
        use_ssl: true,
        username,
        password,
        consent: { accepted: true, scope: CONSENT_SCOPE, consent_version: CONSENT_VERSION },
      });
      connectionCreatedRef.current = true;
      // Live-test right after save so the result panel shows the real verification outcome.
      const tested = await testMyConnection(created.id);
      setResult(tested);
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        onSessionExpired();
        return;
      }
      const status = error instanceof AuthRequestError ? error.status : 0;
      setErrorMessage(messageFor(status));
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    consented &&
    email.includes("@") &&
    host.trim().length > 0 &&
    password.length > 0 &&
    !submitting;
  const helpUrl = appPasswordHelpUrl(email);

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
            aria-label={`Connect your ${connectorLabel}`}
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md overflow-y-auto border-l border-white/50 bg-brand-bg/80 p-6 shadow-xl backdrop-blur-xl"
          >
            <div className="mb-6 flex items-center justify-between">
              <h2 className="text-h3 font-bold text-text-primary">
                {result === null ? `Connect your ${connectorLabel}` : "Mailbox connected"}
              </h2>
              <button
                type="button"
                onClick={handleClose}
                disabled={submitting}
                aria-label="Close"
                className="rounded-lg px-2 py-1 text-text-muted transition-colors duration-200 hover:text-brand-red disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-text-muted"
              >
                ✕
              </button>
            </div>

            {result !== null ? (
              <ConnectResult connection={result} onDone={handleClose} />
            ) : (
              <form className="space-y-4" onSubmit={handleSubmit} noValidate>
                <ConsentNotice checked={consented} onChange={setConsented} />

                <TextField
                  id="me-mailbox-email"
                  label="Email address"
                  type="email"
                  value={email}
                  onChange={handleEmailChange}
                  placeholder="you@your-company.de"
                />
                <TextField
                  id="me-mailbox-name"
                  label="Display name (optional)"
                  value={displayName}
                  onChange={setDisplayName}
                  placeholder="My work mailbox"
                />

                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label
                      htmlFor="me-mailbox-password"
                      className="block text-sm font-medium text-text-secondary"
                    >
                      App password
                    </label>
                    {helpUrl !== null && (
                      <a
                        href={helpUrl}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-xs font-medium text-brand-blue transition-colors duration-200 hover:text-brand-teal"
                      >
                        How to get one ↗
                      </a>
                    )}
                  </div>
                  <div className="relative">
                    <input
                      id="me-mailbox-password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      autoComplete="off"
                      onChange={(event) => setPassword(event.target.value)}
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
                    Not your normal password — an app-specific password from your mail provider.
                  </p>
                </div>

                <div>
                  <button
                    type="button"
                    onClick={() => setShowAdvanced((shown) => !shown)}
                    className="text-xs font-medium text-text-muted transition-colors duration-200 hover:text-text-primary"
                  >
                    {showAdvanced ? "Hide" : "Show"} server settings (auto-detected)
                  </button>
                  {showAdvanced && (
                    <div className="mt-2 grid grid-cols-3 gap-2">
                      <div className="col-span-2">
                        <TextField
                          id="me-mailbox-host"
                          label="IMAP host"
                          value={host}
                          onChange={(value) => {
                            setHost(value);
                            setServerEdited(true);
                          }}
                        />
                      </div>
                      <div>
                        <label
                          htmlFor="me-mailbox-port"
                          className="mb-1 block text-sm font-medium text-text-secondary"
                        >
                          Port
                        </label>
                        <input
                          id="me-mailbox-port"
                          type="number"
                          value={port}
                          onChange={(event) => {
                            setPort(Number(event.target.value) || 993);
                            setServerEdited(true);
                          }}
                          className={INPUT_CLASS}
                        />
                      </div>
                    </div>
                  )}
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
                  {submitting ? "Connecting…" : "Connect mailbox"}
                </button>
              </form>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

/** The explicit consent block — a checkbox + short GDPR Art. 7 text the user must accept (HITL). */
function ConsentNotice({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}): React.JSX.Element {
  return (
    <div className="rounded-xl border border-brand-purple/30 bg-brand-purple/5 p-4">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          aria-label="I consent to One AI accessing this mailbox"
          className="mt-0.5 h-4 w-4 shrink-0 accent-brand-purple"
        />
        <span className="text-xs leading-relaxed text-text-secondary">
          I consent to One AI securely reading this mailbox to build my personal AI. Only I can see
          its contents — my administrator sees connection health, never my email. I can disconnect
          and erase this data at any time.
        </span>
      </label>
    </div>
  );
}

/** The post-submit result panel: a live ✓ when authentication succeeded, else the sign-in error. */
function ConnectResult({
  connection,
  onDone,
}: {
  connection: Connection;
  onDone: () => void;
}): React.JSX.Element {
  const connected = connection.status === "connected";
  const statusRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    statusRef.current?.focus();
  }, []);
  return (
    <div className="animate-fade-in space-y-4">
      <div
        className={`rounded-xl border p-4 ${
          connected
            ? "border-brand-teal/30 bg-brand-teal/10"
            : "border-brand-red/30 bg-brand-red/10"
        }`}
      >
        <p
          ref={statusRef}
          tabIndex={-1}
          className={`font-semibold outline-none ${connected ? "text-brand-teal" : "text-brand-red"}`}
        >
          {connected ? "✓ Connected" : "Sign-in failed"}
        </p>
        <p className="mt-1 text-sm text-text-secondary">
          {connected
            ? `One AI can reach ${connection.username}. It will start learning from this mailbox.`
            : (connection.last_error ??
              "We saved the mailbox but couldn't sign in. Check the app password, then disconnect and re-add it.")}
        </p>
      </div>
      <button
        type="button"
        onClick={onDone}
        className="w-full rounded-xl border border-white/50 bg-white/65 px-8 py-3 font-semibold text-text-primary backdrop-blur-xl transition-all duration-200 hover:bg-white/80"
      >
        Done
      </button>
    </div>
  );
}
