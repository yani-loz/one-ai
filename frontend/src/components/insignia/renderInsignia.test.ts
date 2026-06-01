/**
 * Tests for the insignia renderer against a recording fake Canvas 2D context — verifies it
 * draws (without throwing) across every branch, rank, and connection/charge variant.
 * A live canvas isn't available in jsdom, so we assert on the drawing calls instead.
 */
import { describe, expect, it } from "vitest";

import { generateInsignia, type AgentBranch } from "./generateInsignia";
import { renderInsignia, type AgentActivity } from "./renderInsignia";

/** Minimal Canvas 2D stub that counts the geometry calls renderInsignia makes. */
function createFakeContext(): { ctx: CanvasRenderingContext2D; counts: { arc: number; stroke: number; fill: number } } {
  const counts = { arc: 0, stroke: 0, fill: 0 };
  const ctx = {
    setTransform() {},
    clearRect() {},
    beginPath() {},
    closePath() {},
    moveTo() {},
    lineTo() {},
    arc() {
      counts.arc++;
    },
    stroke() {
      counts.stroke++;
    },
    fill() {
      counts.fill++;
    },
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D;
  return { ctx, counts };
}

const BRANCHES: AgentBranch[] = ["retrieval", "analysis", "action", "orchestrator"];

describe("renderInsignia", () => {
  it("test_draws_for_every_branch_rank_and_seed_without_throwing", () => {
    for (const branch of BRANCHES) {
      for (const rank of [1, 2, 3] as const) {
        for (let seed = 0; seed < 20; seed++) {
          const { ctx, counts } = createFakeContext();
          const spec = generateInsignia(seed, branch, rank);

          renderInsignia(ctx, spec, { size: 120, dpr: 2, time: 0 });

          expect(counts.arc).toBeGreaterThan(0); // nodes + frame drawn
          expect(counts.stroke).toBeGreaterThan(0); // frame / threads drawn
        }
      }
    }
  });

  it("test_every_activity_animation_renders", () => {
    const activities: AgentActivity[] = ["idle", "thinking", "searching", "acting"];
    for (const activity of activities) {
      const { ctx, counts } = createFakeContext();

      renderInsignia(ctx, generateInsignia(3, "orchestrator", 3), { size: 96, dpr: 1, time: 1.5, activity });

      expect(counts.arc).toBeGreaterThan(0);
    }
  });

  it("test_assembly_birth_frame_renders_mid_convergence", () => {
    const { ctx, counts } = createFakeContext();

    // time (0.5) < assembleSeconds (2.2) → particles still converging.
    renderInsignia(ctx, generateInsignia(7, "retrieval", 3), { size: 120, dpr: 2, time: 0.5, assembleSeconds: 2.2 });

    expect(counts.arc).toBeGreaterThan(0);
  });
});
