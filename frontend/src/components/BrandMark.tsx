/**
 * Role: The One AI brand mark — the apex insignia crest, born from converging particles.
 *       The grandest member of the agent-insignia family (full aurora · six-fold web · top
 *       rank); the app's primary symbol on the login and home screens.
 * Used by: identity/LoginPage.tsx, HomePage.tsx.
 * Depends on: ./insignia/generateInsignia (generateApexInsignia), ./insignia/renderInsignia.
 * Key invariants:
 *   - Decorative (aria-hidden): the visible "One AI" wordmark carries the meaning.
 *   - Deterministic from a fixed seed → the same crest every load.
 *   - Honors prefers-reduced-motion (static, fully-assembled crest — no birth, no loop) and
 *     degrades where Canvas 2D is unavailable (e.g. jsdom): draws nothing, no throw.
 *   - The RAF loop is cancelled on cleanup (StrictMode-safe).
 */
import { useEffect, useMemo, useRef } from "react";

import { generateApexInsignia } from "./insignia/generateInsignia";
import { renderInsignia, type AgentActivity } from "./insignia/renderInsignia";

/** Fixed seed → the same apex crest every load. */
const APEX_SEED = 42;

interface BrandMarkProps {
  /** CSS-pixel size of the square mark. */
  size?: number;
  /** Tailwind positioning/margin classes for the wrapper. */
  className?: string;
  /** Override the crest seed. */
  seed?: number;
  /** Seconds for the particle "birth" on mount. */
  assembleSeconds?: number;
  /** Drive the crest's motion — e.g. "thinking" while a request is in flight. Default "idle". */
  activity?: AgentActivity;
}

/** True when the user has asked the OS to minimize non-essential motion. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Renders the One AI apex crest brand mark. */
export function BrandMark({
  size = 112,
  className = "mx-auto",
  seed = APEX_SEED,
  assembleSeconds = 2.2,
  activity = "idle",
}: BrandMarkProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const spec = useMemo(() => generateApexInsignia(seed), [seed]);

  // Read activity through a ref so toggling it never restarts the loop (the birth plays once).
  const activityRef = useRef<AgentActivity>(activity);
  useEffect(() => {
    activityRef.current = activity;
  }, [activity]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return; // No Canvas 2D (e.g. jsdom) → draw nothing, don't throw.

    const dpr = Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 2);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);

    if (prefersReducedMotion() || typeof requestAnimationFrame !== "function") {
      renderInsignia(ctx, spec, { size, dpr, time: 0, activity: activityRef.current });
      return;
    }

    let rafId = 0;
    let startMs = 0;
    const tick = (nowMs: number): void => {
      if (startMs === 0) startMs = nowMs;
      renderInsignia(ctx, spec, {
        size,
        dpr,
        time: (nowMs - startMs) / 1000,
        assembleSeconds,
        activity: activityRef.current,
      });
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [spec, size, assembleSeconds]);

  return (
    <div className={`relative ${className}`} style={{ width: size, height: size }} aria-hidden="true">
      <canvas
        ref={canvasRef}
        style={{ width: size, height: size, filter: "drop-shadow(0 2px 8px rgba(59,130,246,0.25))" }}
        className="relative"
      />
    </div>
  );
}
