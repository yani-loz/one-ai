/**
 * Role: Draw a One AI agent insignia from its spec onto a Canvas 2D context — a ring
 *       frame (with rank rings + pips), a rotationally-symmetric charge of glowing
 *       particle-nodes wired by threads, all in the branch's aurora colour. Particles
 *       forming an emblem: the product's "many → one" DNA at agent scale.
 * Used by: components/AgentInsignia.tsx, the dev insignia gallery.
 * Depends on: ./generateInsignia (InsigniaSpec).
 * Key invariants:
 *   - Self-contained per call: resets the transform and clears the canvas each frame.
 *   - `time = 0` yields a valid static emblem (used for stills / reduced motion).
 *   - Pure drawing: no DOM/layout reads.
 */
import type { AgentBranch, InsigniaSpec } from "./generateInsignia";

const TWO_PI = Math.PI * 2;
type Rgb = [number, number, number];

/** Sample a palette cyclically at `t` ∈ [0,1): t=0 → inks[0], wrapping back at t=1. */
function auroraColorAt(t: number, inks: Rgb[]): Rgb {
  const n = inks.length;
  const scaled = ((((t % 1) + 1) % 1) * n) % n;
  const i = Math.floor(scaled) % n;
  const j = (i + 1) % n;
  const f = scaled - Math.floor(scaled);
  const lerp = (a: number, b: number): number => Math.round(a + (b - a) * f);
  return [lerp(inks[i][0], inks[j][0]), lerp(inks[i][1], inks[j][1]), lerp(inks[i][2], inks[j][2])];
}

/** Branch → primary aurora colour (orchestrator uses the full sweep for its nodes). */
const BRANCH_PRIMARY: Record<AgentBranch, string> = {
  retrieval: "#0d9488",
  analysis: "#3b82f6",
  action: "#8b5cf6",
  orchestrator: "#3b82f6",
};
const AURORA: Rgb[] = [
  [13, 148, 136],
  [59, 130, 246],
  [139, 92, 246],
];

/**
 * What the agent is doing — selects the activity animation:
 * - idle: alive but calm (slow drift + circulating signal).
 * - thinking: contained internal processing (charge concentrates, nodes flicker fast).
 * - searching: reaching out (rotating scan beam + outward sonar pings).
 * - acting: decisive discharge (pulses fire from core out to the tips, on a beat).
 */
export type AgentActivity = "idle" | "thinking" | "searching" | "acting";

/** Options for one render pass. */
export interface InsigniaRenderOptions {
  size: number;
  dpr: number;
  /** Seconds since mount; 0 = static. Drives the animation. */
  time?: number;
  /** The agent's current activity (default "idle"). */
  activity?: AgentActivity;
  /** If > 0, the emblem is "born" over this many seconds — particles converge into it. */
  assembleSeconds?: number;
}

function hexToRgb(hex: string): Rgb {
  const value = parseInt(hex.replace("#", ""), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** Colour of a node at `angle` — aurora-swept for orchestrators, else the branch colour. */
function nodeColor(branch: AgentBranch, primary: Rgb, angle: number): Rgb {
  return branch === "orchestrator" ? auroraColorAt(angle / TWO_PI, AURORA) : primary;
}

/** Draw a glowing particle: soft halo + bright core. */
function drawNode(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  coreR: number,
  rgb: Rgb,
  alpha: number,
): void {
  ctx.fillStyle = `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  ctx.globalAlpha = alpha * 0.18;
  ctx.beginPath();
  ctx.arc(x, y, coreR * 2.6, 0, TWO_PI);
  ctx.fill();
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.arc(x, y, coreR, 0, TWO_PI);
  ctx.fill();
}

interface NodePoint {
  x: number;
  y: number;
  angle: number;
}

/** Wrap an angle to (−π, π]. */
function wrapAngle(a: number): number {
  let x = a % TWO_PI;
  if (x > Math.PI) x -= TWO_PI;
  if (x < -Math.PI) x += TWO_PI;
  return x;
}

/** Compute the inner/outer node positions for the charge (rotated by `spin`, scaled by `scale`). */
function chargeNodes(
  spec: InsigniaSpec,
  cx: number,
  cy: number,
  frameR: number,
  spin: number,
  scale: number,
): { inner: NodePoint[]; outer: NodePoint[] } {
  const inner: NodePoint[] = [];
  const outer: NodePoint[] = [];
  const innerR = frameR * spec.innerRadius * scale;
  const outerR = frameR * spec.outerRadius * scale;
  for (let j = 0; j < spec.symmetry; j++) {
    const angle = spec.rotationOffset + spin + (j / spec.symmetry) * TWO_PI;
    inner.push({ x: cx + Math.cos(angle) * innerR, y: cy + Math.sin(angle) * innerR, angle });
    if (spec.hasOuterNodes) {
      outer.push({ x: cx + Math.cos(angle) * outerR, y: cy + Math.sin(angle) * outerR, angle });
    }
  }
  return { inner, outer };
}

/** Stroke the threads wiring the charge together, per the connection pattern. */
function drawThreads(
  ctx: CanvasRenderingContext2D,
  spec: InsigniaSpec,
  cx: number,
  cy: number,
  inner: NodePoint[],
  outer: NodePoint[],
  primary: Rgb,
  width: number,
  alphaScale: number,
): void {
  ctx.strokeStyle = `rgb(${primary[0]}, ${primary[1]}, ${primary[2]})`;
  ctx.lineWidth = width;
  ctx.globalAlpha = 0.4 * alphaScale;

  if (spec.connection === "polygon" || spec.connection === "web") {
    ctx.beginPath();
    inner.forEach((node, i) => (i === 0 ? ctx.moveTo(node.x, node.y) : ctx.lineTo(node.x, node.y)));
    ctx.closePath();
    ctx.stroke();
  }
  if (spec.connection === "spoke" || spec.connection === "web") {
    const tips = outer.length > 0 ? outer : inner;
    for (const tip of tips) {
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(tip.x, tip.y);
      ctx.stroke();
    }
  }
  if (spec.connection === "web" && outer.length > 0) {
    for (let j = 0; j < inner.length; j++) {
      ctx.beginPath();
      ctx.moveTo(inner[j].x, inner[j].y);
      ctx.lineTo(outer[j].x, outer[j].y);
      ctx.stroke();
    }
  }
}

/** Draw the ring frame, the rank rings, and the rank pips above the crest. */
function drawFrame(
  ctx: CanvasRenderingContext2D,
  spec: InsigniaSpec,
  cx: number,
  cy: number,
  frameR: number,
  primary: Rgb,
  lineWidth: number,
  alphaScale: number,
): void {
  const stroke = `rgb(${primary[0]}, ${primary[1]}, ${primary[2]})`;
  ctx.strokeStyle = stroke;
  ctx.fillStyle = stroke;

  ctx.globalAlpha = 0.85 * alphaScale;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  ctx.arc(cx, cy, frameR, 0, TWO_PI);
  ctx.stroke();

  if (spec.rank >= 2) {
    ctx.globalAlpha = 0.45 * alphaScale;
    ctx.lineWidth = lineWidth * 0.6;
    ctx.beginPath();
    ctx.arc(cx, cy, frameR * 1.1, 0, TWO_PI);
    ctx.stroke();
  }

  // Rank pips, centred above the crest like stars/chevrons.
  const pip = frameR * 0.05;
  ctx.globalAlpha = alphaScale;
  for (let i = 0; i < spec.rank; i++) {
    const spread = (i - (spec.rank - 1) / 2) * 0.26;
    const angle = -Math.PI / 2 + spread;
    const px = cx + Math.cos(angle) * frameR * 1.24;
    const py = cy + Math.sin(angle) * frameR * 1.24;
    ctx.beginPath();
    ctx.arc(px, py, pip, 0, TWO_PI);
    ctx.fill();
  }
}

const easeOut = (p: number): number => 1 - Math.pow(1 - p, 3);

/** Deterministic scattered spawn point for node `k` — where its particle starts before it
 *  converges into place (the "many → one" birth, shared by every emblem). */
function scatterStart(seed: number, k: number, cx: number, cy: number, size: number): { x: number; y: number } {
  let h = (Math.imul(seed + 1, 73856093) ^ Math.imul(k + 1, 19349663)) >>> 0;
  const r1 = (h % 1000) / 1000;
  h = (Math.imul(h, 1103515245) + 12345) >>> 0;
  const r2 = (h % 1000) / 1000;
  const angle = r1 * TWO_PI;
  const dist = (0.16 + r2 * 0.34) * size;
  return { x: cx + Math.cos(angle) * dist, y: cy + Math.sin(angle) * dist };
}

/** Searching: expanding sonar rings rippling outward past the frame. */
function drawSonarPings(ctx: CanvasRenderingContext2D, cx: number, cy: number, frameR: number, strokeCss: string, time: number): void {
  ctx.strokeStyle = strokeCss;
  ctx.lineWidth = 1.5;
  for (let k = 0; k < 2; k++) {
    const p = ((time / 1.6 + k / 2) % 1 + 1) % 1;
    ctx.globalAlpha = (1 - p) * 0.4;
    ctx.beginPath();
    ctx.arc(cx, cy, frameR * (1 + p * 0.7), 0, TWO_PI);
    ctx.stroke();
  }
}

/** Searching: a bright radial beam sweeping around like a radar scanner. */
function drawScanBeam(ctx: CanvasRenderingContext2D, cx: number, cy: number, frameR: number, strokeCss: string, beamAngle: number): void {
  ctx.strokeStyle = strokeCss;
  ctx.lineWidth = 2;
  ctx.globalAlpha = 0.55;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(beamAngle) * frameR * 0.95, cy + Math.sin(beamAngle) * frameR * 0.95);
  ctx.stroke();
}

/** Acting: bright pulses discharging from the core out along each thread to the tips. */
function drawActionPulse(ctx: CanvasRenderingContext2D, cx: number, cy: number, tips: NodePoint[], beat: number, fillCss: string, dotR: number): void {
  const p = easeOut(beat);
  ctx.fillStyle = fillCss;
  ctx.globalAlpha = (1 - beat) * 0.9;
  for (const tip of tips) {
    ctx.beginPath();
    ctx.arc(cx + (tip.x - cx) * p, cy + (tip.y - cy) * p, dotR, 0, TWO_PI);
    ctx.fill();
  }
}

/**
 * Render an agent insignia for one frame.
 *
 * Contract: clears and redraws the whole canvas; safe to call once or every frame.
 * `options.activity` selects the motion (idle/thinking/searching/acting).
 */
export function renderInsignia(
  ctx: CanvasRenderingContext2D,
  spec: InsigniaSpec,
  options: InsigniaRenderOptions,
): void {
  const { size, dpr, time = 0 } = options;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);

  const animated = time > 0;
  const activity: AgentActivity = options.activity ?? "idle";
  const cx = size / 2;
  const cy = size / 2;
  const frameR = size * 0.36;
  const primary = hexToRgb(BRANCH_PRIMARY[spec.branch]);
  const primaryCss = `rgb(${primary[0]}, ${primary[1]}, ${primary[2]})`;

  // Per-activity drift speed + whether the charge concentrates inward.
  let spinSpeed = 0.12;
  let chargeScale = 1;
  if (activity === "thinking") {
    spinSpeed = 0.05;
    chargeScale = 0.92 + 0.05 * Math.sin(time * 2.6);
  } else if (activity === "searching") {
    spinSpeed = 0;
  } else if (activity === "acting") {
    spinSpeed = 0.08;
  }
  const spin = animated ? time * spinSpeed : 0;
  const { inner, outer } = chargeNodes(spec, cx, cy, frameR, spin, animated ? chargeScale : 1);
  const tips = outer.length > 0 ? outer : inner;
  const beamAngle = time * 2.6;
  const beat = (time % 0.9) / 0.9;

  // Birth: particles converge into the emblem; frame + threads fade in as they arrive.
  const assemble = options.assembleSeconds ?? 0;
  const born = animated && assemble > 0 ? Math.min(1, time / assemble) : 1;
  const bornEase = easeOut(born);

  drawFrame(ctx, spec, cx, cy, frameR, primary, size * 0.012, born);
  drawThreads(ctx, spec, cx, cy, inner, outer, primary, size * 0.008, born * born);

  if (animated && born >= 1 && activity === "searching") {
    drawSonarPings(ctx, cx, cy, frameR, primaryCss, time);
    drawScanBeam(ctx, cx, cy, frameR, primaryCss, beamAngle);
  } else if (animated && born >= 1 && activity === "acting") {
    drawActionPulse(ctx, cx, cy, tips, beat, primaryCss, size * 0.022);
  }

  const coreNodeR = size * 0.024;
  const all = [...outer, ...inner];
  all.forEach((node, i) => {
    let brightness = 1;
    let radiusFactor = 1;
    if (animated && activity === "thinking") {
      const flicker = Math.max(0, Math.sin(time * 7 + i * 1.9) * Math.cos(time * 3.1 + i * 0.7));
      brightness = 0.4 + 0.6 * flicker;
      radiusFactor = 0.85 + 0.3 * flicker;
    } else if (animated && activity === "searching") {
      const flare = Math.exp(-(wrapAngle(node.angle - beamAngle) ** 2) / 0.12);
      brightness = 0.35 + 0.65 * flare;
      radiusFactor = 1 + 0.7 * flare;
    } else if (animated && activity === "acting") {
      const arrival = Math.exp(-((beat - 0.82) ** 2) / 0.015);
      brightness = 0.45 + 0.55 * arrival;
      radiusFactor = 1 + 0.3 * arrival; // crisp flare, not a balloon
    } else if (animated) {
      const flare = Math.exp(-(wrapAngle(node.angle - time * 0.9) ** 2) / 0.4);
      const breathe = 0.82 + 0.18 * Math.sin(time * 1.8 + i);
      brightness = Math.min(1, (0.55 + 0.45 * flare) * breathe);
      radiusFactor = 1 + 0.4 * flare;
    }
    let nx = node.x;
    let ny = node.y;
    let fade = 1;
    if (born < 1) {
      const start = scatterStart(spec.seed, i, cx, cy, size);
      nx = start.x + (node.x - start.x) * bornEase;
      ny = start.y + (node.y - start.y) * bornEase;
      fade = born;
    }
    drawNode(ctx, nx, ny, coreNodeR * radiusFactor, nodeColor(spec.branch, primary, node.angle), brightness * fade);
  });

  if (spec.hasCore) {
    let corePulse = 1;
    let coreScale = 1;
    if (animated && activity === "thinking") {
      corePulse = 0.7 + 0.3 * Math.abs(Math.sin(time * 5));
      coreScale = 1 + 0.15 * Math.sin(time * 5);
    } else if (animated && activity === "acting") {
      corePulse = 0.6 + 0.4 * Math.exp(-(beat ** 2) / 0.02);
    } else if (animated && activity === "searching") {
      corePulse = 0.7;
    } else if (animated) {
      corePulse = 0.85 + 0.15 * Math.sin(time * 1.8);
    }
    drawNode(ctx, cx, cy, size * 0.034 * coreScale, nodeColor(spec.branch, primary, 0), corePulse * born);
  }
  ctx.globalAlpha = 1;
}
