/**
 * Role: Generate a One AI agent's *insignia spec* — a deterministic, military-style emblem
 *       grammar. Every agent shares a ring frame; a seed-driven, rotationally-symmetric
 *       "charge" (particles forming a sigil) makes each one unique; `branch` sets the
 *       colour family and `rank` adds structure (like chevrons/stars). The renderer turns
 *       this spec into the drawn emblem.
 * Used by: components/insignia/renderInsignia.ts, components/AgentInsignia.tsx.
 * Depends on: nothing (pure math, no DOM).
 * Key invariants:
 *   - Same (seed, branch, rank) → identical spec, every time (a stable agent identity).
 *   - `symmetry` ∈ [3,6]; all radii are fractions of the frame radius (resolution-free).
 *   - Higher rank is strictly *more* structure (outer nodes, web links, extra frame ring,
 *     more pips) so seniority is legible at a glance.
 */

const TWO_PI = Math.PI * 2;

/** An agent's branch — its "arm of service", which fixes the colour family. */
export type AgentBranch = "retrieval" | "analysis" | "action" | "orchestrator";

/** Seniority / capability, 1 (junior) → 3 (senior). Adds emblem structure. */
export type AgentRank = 1 | 2 | 3;

/** How the charge's nodes are wired together. */
export type ChargeConnection = "spoke" | "polygon" | "web";

/** The full blueprint for one agent emblem, before drawing. */
export interface InsigniaSpec {
  seed: number;
  branch: AgentBranch;
  rank: AgentRank;
  /** Rotational symmetry order (number of arms), 3–6. */
  symmetry: number;
  /** Orientation of the charge (radians). */
  rotationOffset: number;
  /** Inner node ring radius, as a fraction of the frame radius. */
  innerRadius: number;
  /** Outer node ring radius, as a fraction of the frame radius. */
  outerRadius: number;
  /** Whether each arm carries an outer node (always true for rank ≥ 2). */
  hasOuterNodes: boolean;
  /** Whether a node sits at the centre. */
  hasCore: boolean;
  /** Thread wiring pattern between nodes. */
  connection: ChargeConnection;
}

/** Mulberry32 seeded PRNG → deterministic floats in [0, 1). */
function createSeededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return function nextRandom(): number {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Build the insignia spec for an agent.
 *
 * Contract: deterministic for a given (seed, branch, rank). `branch` and `rank` are
 * semantic inputs (chosen, not random); `seed` is the agent's identity and drives the
 * unique charge. Rank monotonically increases visual complexity.
 */
export function generateInsignia(seed: number, branch: AgentBranch, rank: AgentRank): InsigniaSpec {
  const random = createSeededRandom(seed);

  const symmetry = 3 + Math.floor(random() * 4); // 3–6 arms
  const rotationOffset = random() * TWO_PI;
  const innerRadius = 0.4 + random() * 0.12;
  const outerRadius = 0.68 + random() * 0.12;
  const hasOuterNodes = rank >= 2 || random() < 0.45;
  const hasCore = rank >= 2 || random() < 0.7;
  const connection: ChargeConnection =
    rank >= 3 ? "web" : random() < 0.5 ? "spoke" : "polygon";

  return {
    seed,
    branch,
    rank,
    symmetry,
    rotationOffset,
    innerRadius,
    outerRadius,
    hasOuterNodes,
    hasCore,
    connection,
  };
}

/**
 * The apex emblem — the company's own crest, and the grandest member of the family:
 * a full-aurora orchestrator at top rank with a dense, six-fold web charge. Every agent
 * insignia is a lesser variation of this one (One Company. One AI.).
 */
export function generateApexInsignia(seed: number): InsigniaSpec {
  return {
    ...generateInsignia(seed, "orchestrator", 3),
    symmetry: 6,
    hasOuterNodes: true,
    hasCore: true,
    connection: "web",
    innerRadius: 0.42,
    outerRadius: 0.74,
  };
}
