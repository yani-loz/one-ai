/**
 * Role: Accessibility hook for a custom (non-native-<dialog>) modal — while active it
 *       moves focus into the container, traps Tab within it, closes on Escape, and
 *       restores focus to the previously-focused element on close. Lets a motion-driven
 *       drawer honour the `aria-modal` contract it advertises.
 * Used by: platform/OnboardCompanyDrawer.tsx, platform/OrgErasurePanel.tsx,
 *          admin/CreateUserDrawer.tsx, admin/ConfirmDialog.tsx. Shared across modules, so
 *          it lives in components/ (deep-path import, like BrandMark) rather than in one
 *          feature module — a single focus-trap implementation, never a divergent copy.
 * Depends on: react (useEffect, useRef). No DOM/focus-trap libraries.
 * Key invariants:
 *   - Focus management runs ONCE per activation (keyed on `active`), so typing inside the
 *     dialog never re-grabs focus; `onClose` is read through a ref to stay off the deps.
 *   - On deactivation/unmount it restores focus to whatever was focused before opening.
 *   - Escape calls `onClose`; Tab / Shift+Tab wrap within the container's focusables.
 */
import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Wire modal focus behaviour to a container element.
 *
 * Contract: attach the returned ref to the dialog container. When `active` becomes true,
 * focus moves to the first focusable (or the container); Escape and Tab-trapping are
 * handled while active; on close, focus returns to the prior element.
 */
export function useDialogA11y<T extends HTMLElement>(
  active: boolean,
  onClose: () => void,
): React.RefObject<T | null> {
  const containerRef = useRef<T | null>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (container === null) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const getFocusables = (): HTMLElement[] =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    (getFocusables()[0] ?? container).focus();

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = getFocusables();
      if (items.length === 0) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    }

    container.addEventListener("keydown", handleKeyDown);
    return () => {
      container.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [active]);

  return containerRef;
}
