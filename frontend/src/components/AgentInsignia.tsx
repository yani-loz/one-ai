/**
 * Role: Render a single agent's insignia as a living emblem — the reusable avatar for any
 *       One AI agent (and the personal-AI of each employee). Wraps the generative spec +
 *       canvas renderer, with a gentle node shimmer so the emblem feels alive.
 * Used by: agent avatars, presence indicators, the "army" views (and the dev gallery).
 * Depends on: ./insignia/generateInsignia, ./insignia/renderInsignia.
 * Key invariants:
 *   - Decorative by default (aria-hidden unless a label is given); deterministic from
 *     (seed, branch, rank) — an agent always wears the same insignia.
 *   - Honors prefers-reduced-motion (static emblem) and degrades where Canvas 2D is
 *     unavailable (e.g. jsdom): draws nothing, no throw. RAF cancelled on cleanup.
 */
import { useEffect, useMemo, useRef } from "react";

import { generateInsignia, type AgentBranch, type AgentRank } from "./insignia/generateInsignia";
import { renderInsignia, type AgentActivity } from "./insignia/renderInsignia";

interface AgentInsigniaProps {
  /** The agent's stable identity — same seed → same emblem. */
  seed: number;
  /** Branch (colour family). */
  branch: AgentBranch;
  /** Rank (1–3). */
  rank: AgentRank;
  /** What the agent is currently doing (drives the animation). Default "idle". */
  activity?: AgentActivity;
  /** If > 0, the emblem is "born" — its particles converge into place over this many seconds. */
  assembleSeconds?: number;
  /** CSS-pixel size of the square emblem. */
  size?: number;
  /** Tailwind positioning/margin classes for the wrapper. */
  className?: string;
  /** Accessible label; when set the emblem is announced instead of hidden. */
  label?: string;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Renders one agent's living insignia emblem. */
export function AgentInsignia({
  seed,
  branch,
  rank,
  activity = "idle",
  assembleSeconds = 0,
  size = 64,
  className = "",
  label,
}: AgentInsigniaProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const spec = useMemo(() => generateInsignia(seed, branch, rank), [seed, branch, rank]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return; // No Canvas 2D (e.g. jsdom) → draw nothing, don't throw.

    const dpr = Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 2);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);

    if (prefersReducedMotion() || typeof requestAnimationFrame !== "function") {
      renderInsignia(ctx, spec, { size, dpr, time: 0, activity });
      return;
    }

    let rafId = 0;
    let startMs = 0;
    const tick = (nowMs: number): void => {
      if (startMs === 0) startMs = nowMs;
      renderInsignia(ctx, spec, { size, dpr, time: (nowMs - startMs) / 1000, activity, assembleSeconds });
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [spec, size, activity, assembleSeconds]);

  return (
    <canvas
      ref={canvasRef}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size, filter: "drop-shadow(0 1px 4px rgba(59,130,246,0.2))" }}
      className={className}
    />
  );
}
