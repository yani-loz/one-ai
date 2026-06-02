/**
 * Role: Generic confirm modal for a reversible-but-consequential company-admin action
 *       (deactivate a user, demote your own admin access). Title + message + one confirm
 *       button; the parent owns the action, the busy flag, and any error message.
 * Used by: AdminConsolePage.tsx.
 * Depends on: react-dom (createPortal), motion (pop + reduced-motion), ../components/
 *             useDialogA11y, the aurora Tailwind theme.
 * Key invariants:
 *   - PORTALED to document.body: the fixed overlay must escape the console's nested glass /
 *     backdrop-blur (transform) stacking contexts, or a lower element intercepts the confirm
 *     click (the same trap OrgErasurePanel hit in live testing).
 *   - Purely controlled: it renders only when `open`; it holds no action state of its own, so
 *     the parent decides success/refresh and passes `busy`/`errorMessage` back in.
 *   - `tone="danger"` themes the confirm button red; otherwise it is the teal→blue gradient.
 */
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { useDialogA11y } from "../components/useDialogA11y";

const POP = { duration: 0.3, ease: [0.32, 0.72, 0, 1] } as const;

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  /** "danger" themes the confirm button red (destructive); "default" uses the brand gradient. */
  tone?: "default" | "danger";
  busy: boolean;
  errorMessage: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Render a confirm modal.
 *
 * Contract: shown only while `open`. `onConfirm` runs the parent's action (which sets `busy`
 * and may set `errorMessage`); `onCancel` dismisses it. The confirm button is disabled while
 * `busy`. Escape / backdrop click / Cancel all call `onCancel`.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  tone = "default",
  busy,
  errorMessage,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): React.JSX.Element {
  const reduceMotion = useReducedMotion();
  const dialogRef = useDialogA11y<HTMLDivElement>(open, onCancel);

  const confirmClass =
    tone === "danger"
      ? "bg-brand-red text-white"
      : "bg-gradient-to-r from-brand-teal to-brand-blue text-white";

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onCancel}
            data-testid="confirm-backdrop"
            className="fixed inset-0 z-40 bg-text-primary/20 backdrop-blur-sm"
            aria-hidden="true"
          />
          {/* pointer-events-none lets a click in the padding area fall THROUGH to the backdrop
              (so an outside click dismisses); the dialog re-enables its own pointer events. */}
          <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center p-6">
            <motion.div
              ref={dialogRef}
              initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
              transition={reduceMotion ? { duration: 0 } : POP}
              role="dialog"
              aria-modal="true"
              aria-label={title}
              className="pointer-events-auto w-full max-w-md rounded-xl border border-white/50 bg-brand-bg/90 p-6 shadow-xl backdrop-blur-xl"
            >
              <h2 className="text-h3 font-bold text-text-primary">{title}</h2>
              <p className="mt-2 text-sm text-text-secondary">{message}</p>

              {errorMessage !== null && (
                <p role="alert" className="mt-3 text-sm text-brand-red">
                  {errorMessage}
                </p>
              )}

              <div className="mt-5 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={onCancel}
                  className="rounded-lg border border-white/50 bg-white/50 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={onConfirm}
                  className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all duration-200 hover:scale-[1.02] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 ${confirmClass}`}
                >
                  {busy ? "Working…" : confirmLabel}
                </button>
              </div>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
