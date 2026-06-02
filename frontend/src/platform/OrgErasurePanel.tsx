/**
 * Role: Platform-side erasure + compliance control on the org detail screen. Exports the
 *       compliance record (download) and erases the company behind a type-the-slug
 *       confirmation modal — the irreversible-action guard, mirroring the backend.
 * Used by: OrganizationDetailPage (rendered inside an "Erasure & compliance" Section).
 * Depends on: motion, ../identity (AuthRequestError), ./platformClient,
 *             ../components/useDialogA11y, ./types, the aurora theme.
 * Key invariants:
 *   - Erase is gated client-side too: "Erase permanently" stays disabled until the operator
 *     types the EXACT slug + a reason + their own password (sudo-style re-auth). The backend
 *     re-validates all three. A legal hold (409), a wrong password (403), or a slug mismatch
 *     (400) surfaces as a message; a 401 bubbles to onAuthExpired (the password check is 403,
 *     not 401, so a wrong password never logs the admin out).
 *   - On success the parent reloads (onErased) — the org re-renders as `offboarded`, and an
 *     already-erased org offers export only.
 *   - Metadata only: the export bundle is actions/metadata, never tenant content.
 *   - The confirm modal is PORTALED to document.body: its fixed overlay must escape the
 *     nested glass/`backdrop-blur` (transform) stacking contexts of the detail page, or a
 *     lower element intercepts clicks on the destructive button (caught in live testing).
 */
import { useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { AuthRequestError } from "../identity";
import { eraseOrganization, exportCompliance } from "./platformClient";
import type { OrganizationDetail } from "./types";
import { useDialogA11y } from "../components/useDialogA11y";

const POP = { duration: 0.3, ease: [0.32, 0.72, 0, 1] } as const;

/** Trigger a browser download of `data` as pretty JSON (best-effort; no-op if unsupported). */
function downloadJson(filename: string, data: unknown): void {
  if (typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

interface Props {
  org: OrganizationDetail;
  /** Called after a successful erase so the parent reloads the (now offboarded) detail. */
  onErased: () => void;
  /** Called on a 401 so the parent tears the session down. Must be stable. */
  onAuthExpired: () => void;
}

export function OrgErasurePanel({ org, onErased, onAuthExpired }: Props): React.JSX.Element {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [slugInput, setSlugInput] = useState("");
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [exported, setExported] = useState(false);
  const reduceMotion = useReducedMotion();

  function closeDialog(): void {
    setDialogOpen(false);
    setSlugInput("");
    setReason("");
    setPassword("");
    setErrorMessage(null);
  }

  const dialogRef = useDialogA11y<HTMLDivElement>(dialogOpen, closeDialog);
  const alreadyErased = org.status === "offboarded";
  const canErase =
    slugInput === org.slug && reason.trim().length > 0 && password.length > 0 && !busy;

  async function handleExport(): Promise<void> {
    setBusy(true);
    setErrorMessage(null);
    try {
      const bundle = await exportCompliance(org.id);
      downloadJson(`compliance-${org.slug}.json`, bundle);
      setExported(true);
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        onAuthExpired();
        return;
      }
      setErrorMessage("Couldn't export the compliance record. Check the API and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function handleErase(): Promise<void> {
    setBusy(true);
    setErrorMessage(null);
    try {
      await eraseOrganization(org.id, reason.trim(), slugInput, password);
      closeDialog();
      onErased();
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        onAuthExpired();
        return;
      }
      const status = error instanceof AuthRequestError ? error.status : 0;
      setErrorMessage(
        status === 409
          ? "This company is under legal hold — clear the hold before erasing."
          : status === 403
            ? "Incorrect password. Please re-enter your password."
            : "Couldn't erase the company. Check the details and that the API is running.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="mb-3 text-xs text-text-muted">
        Export the compliance record (metadata + audit trail), or permanently erase this
        company's personal data. A legal hold blocks erasure; the audit trail is retained.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void handleExport()}
          className="rounded-lg border border-white/50 bg-white/50 px-3 py-2 text-xs font-medium text-text-primary transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
        >
          {exported ? "Exported ✓" : "Export compliance record"}
        </button>
        {!alreadyErased && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setDialogOpen(true)}
            className="rounded-lg border border-brand-red/40 bg-brand-red/10 px-3 py-2 text-xs font-medium text-brand-red transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
          >
            Erase company
          </button>
        )}
      </div>
      {alreadyErased && (
        <p className="mt-3 text-xs text-text-muted">
          This company has been erased (offboarded). Personal data was deleted; the audit
          trail is retained for compliance.
        </p>
      )}
      {errorMessage !== null && !dialogOpen && (
        <p role="alert" className="mt-3 text-xs text-brand-red">
          {errorMessage}
        </p>
      )}

      {createPortal(
        <AnimatePresence>
          {dialogOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={closeDialog}
                className="fixed inset-0 z-40 bg-text-primary/20 backdrop-blur-sm"
                aria-hidden="true"
              />
            <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
              <motion.div
                ref={dialogRef}
                initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
                transition={reduceMotion ? { duration: 0 } : POP}
                role="dialog"
                aria-modal="true"
                aria-label="Erase company"
                className="w-full max-w-md rounded-xl border border-brand-red/30 bg-brand-bg/90 p-6 shadow-xl backdrop-blur-xl"
              >
                <h2 className="text-h3 font-bold text-brand-red">Erase {org.name}?</h2>
                <p className="mt-2 text-sm text-text-secondary">
                  This permanently deletes the company's users and sessions and scrubs their
                  personal data. It cannot be undone. The audit trail is retained.
                </p>
                <label
                  htmlFor="erase-slug"
                  className="mt-4 block text-sm font-medium text-text-secondary"
                >
                  Type <span className="font-mono text-text-primary">{org.slug}</span> to confirm
                </label>
                <input
                  id="erase-slug"
                  type="text"
                  value={slugInput}
                  onChange={(event) => setSlugInput(event.target.value)}
                  autoComplete="off"
                  className="mt-1 w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-3 py-2 text-text-primary outline-none focus:border-brand-red focus:shadow-[0_0_0_3px_rgba(220,38,38,0.1)]"
                />
                <label
                  htmlFor="erase-reason"
                  className="mt-3 block text-sm font-medium text-text-secondary"
                >
                  Reason
                </label>
                <input
                  id="erase-reason"
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  maxLength={500}
                  placeholder="e.g. Customer offboarding per contract termination"
                  className="mt-1 w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-3 py-2 text-text-primary outline-none focus:border-brand-red focus:shadow-[0_0_0_3px_rgba(220,38,38,0.1)]"
                />
                <label
                  htmlFor="erase-password"
                  className="mt-3 block text-sm font-medium text-text-secondary"
                >
                  Your password
                </label>
                <input
                  id="erase-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  className="mt-1 w-full rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-3 py-2 text-text-primary outline-none focus:border-brand-red focus:shadow-[0_0_0_3px_rgba(220,38,38,0.1)]"
                />
                {errorMessage !== null && (
                  <p role="alert" className="mt-3 text-sm text-brand-red">
                    {errorMessage}
                  </p>
                )}
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={closeDialog}
                    className="rounded-lg border border-white/50 bg-white/50 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!canErase}
                    onClick={() => void handleErase()}
                    className="rounded-lg bg-brand-red px-4 py-2 text-sm font-semibold text-white transition-all duration-200 hover:scale-[1.02] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100"
                  >
                    {busy ? "Erasing…" : "Erase permanently"}
                  </button>
                </div>
              </motion.div>
            </div>
          </>
        )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
