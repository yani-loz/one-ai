/**
 * Role: iOS-style directional page-transition config (Framer Motion) — forward
 *       navigations push in from the right, "back" mirrors it. Honours
 *       prefers-reduced-motion by collapsing the slide to an instant fade.
 * Used by: App.tsx (spread onto each screen-level motion.div).
 * Depends on: motion/react (Transition type, useReducedMotion), react-router-dom (useLocation).
 * Key invariants:
 *   - Transitioning screens must be `absolute inset-0` so incoming and outgoing
 *     pages overlap during the slide (see frontend-design.md).
 *   - With prefers-reduced-motion the JS slide is suppressed (CSS can't reach
 *     Framer transforms), satisfying the design rule's "disable page slides".
 */
import type { Transition } from "motion/react";
import { useReducedMotion } from "motion/react";
import { useLocation } from "react-router-dom";

/** Apple's ease-out curve — the shared timing for every screen transition. */
const CURVE = { duration: 0.6, ease: [0.32, 0.72, 0, 1] } satisfies Transition;

/** Shape every screen-level motion.div spreads (initial/animate/exit/transition). */
export interface PageTransition {
  initial: { x?: string; opacity?: number };
  animate: { x?: number; opacity?: number };
  exit: { x?: string; opacity?: number };
  transition: Transition;
}

/** Forward navigation: new screen slides in from the right. */
export const pageTransition: PageTransition = {
  initial: { x: "100%" },
  animate: { x: 0 },
  exit: { x: "-30%", opacity: 0 },
  transition: CURVE,
};

/** Back navigation: new screen slides in from the left. */
export const pageTransitionBack: PageTransition = {
  initial: { x: "-100%" },
  animate: { x: 0 },
  exit: { x: "30%", opacity: 0 },
  transition: CURVE,
};

/** Instant opacity-only variant used when the user prefers reduced motion. */
export const reducedMotionTransition: PageTransition = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0 },
};

/**
 * Pick the page transition for the current navigation.
 *
 * Contract: returns an instant opacity-only variant when the user prefers reduced
 * motion; otherwise the "back" variant when the route was entered with
 * `navigate(target, { state: { nav: "back" } })`, else the forward variant.
 */
export function useDirectionalTransition(): PageTransition {
  const prefersReducedMotion = useReducedMotion();
  const goingBack = (useLocation().state as { nav?: "back" } | null)?.nav === "back";

  if (prefersReducedMotion === true) {
    return reducedMotionTransition;
  }
  return goingBack ? pageTransitionBack : pageTransition;
}
