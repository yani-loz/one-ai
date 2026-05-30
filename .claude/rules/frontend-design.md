# Frontend Design Language

## Design Philosophy

Lean, simple, yet tech-forward. The UI should feel **alive** — like a living organism, not a static enterprise dashboard. Nothing pops into existence: content breathes, enters, and responds to touch.

> **Two sources, kept separate by design:**
> - **Colors are One AI's own** — the teal → blue → purple aurora palette (inherited from the Vetera AI system). Never import another project's brand colors.
> - **Motion & surface vocabulary** is refined from the GBS pilot design system — a Tailwind v4 + Framer Motion implementation with a mature animation set. We adopt its *patterns and architecture*, not its colors.

## Architecture — Tailwind v4, token-first

Define the system as CSS tokens in a single `@theme` block in `index.css`. Tailwind v4 turns each token into a utility automatically — `--color-brand-teal` → `bg-brand-teal`, `--animate-aura-pulse` → `animate-aura-pulse`, `--text-h2` → `text-h2`. This keeps the whole design language in one declarative place.

```css
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap");
@import "tailwindcss";

@theme {
  /* — Colors: One AI aurora palette (see table below) — */
  --color-brand-teal: #0d9488;
  --color-brand-blue: #3b82f6;
  --color-brand-purple: #8b5cf6;
  --color-brand-red: #dc2626;
  --color-brand-bg: #f0f4f8;
  --color-text-primary: #1f2937;
  --color-text-secondary: #4b5563;
  --color-text-muted: #6b7280;

  /* — Type scale (size + paired line-height) — */
  --font-sans: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --text-h1: 42px;  --text-h1--line-height: 48px;   /* hero / page title */
  --text-h2: 32px;  --text-h2--line-height: 40px;   /* screen titles     */
  --text-h3: 24px;  --text-h3--line-height: 32px;   /* card titles       */
  /* body → text-base 15px/24px · small → text-sm 13px/20px · caption → text-xs 11px/16px */

  /* — Animation tokens (keyframes defined below) — */
  --animate-gradient-x: gradient-x 15s ease infinite;
  --animate-fade-in: fade-in 0.8s ease-out forwards;
  --animate-shimmer: shimmer 2.5s linear infinite;
  --animate-aura-pulse: aura-pulse 3s ease-in-out infinite;
  --animate-pulse-dot: pulse-dot 2s ease-in-out infinite;
  --animate-clari-pulse: clari-pulse 2s ease-in-out infinite;
  --animate-bounce-soft: bounce-soft 0.6s ease-in-out infinite;
  --animate-slide-in-left: slide-in-left 0.3s ease-out;
  --animate-toast-in: toast-in 0.4s ease;
}
```

**The motion split:**
- **CSS animations** (the `@theme` tokens above) → ambient life + micro-interactions (breathing, shimmer, status pulses).
- **Framer Motion (`motion` package)** → route-level and orchestrated transitions (the directional page push/pop). Reads navigation direction, which CSS can't.
- **Tailwind utilities** → all styling. No CSS-in-JS styling libraries (styled-components / emotion). Framer Motion is *motion*, not styling — it's allowed and expected.

## Color Palette (One AI — do not change)

| Token | Value | Usage |
|-------|-------|-------|
| `brand-teal` | `#0d9488` | Primary accent, success states |
| `brand-blue` | `#3b82f6` | Links, interactive elements |
| `brand-purple` | `#8b5cf6` | AI indicators, premium features, AI-action-pending |
| `brand-red` | `#dc2626` | Errors, destructive actions |
| `brand-bg` | `#f0f4f8` | Page background base |
| `brand-glass` | `rgba(255,255,255,0.65)` | Glass panels |
| `text-primary` | `#1f2937` | Primary text |
| `text-secondary` | `#4b5563` | Secondary text, labels |
| `text-muted` | `#6b7280` | Captions, placeholders, hints |

### Aurora Gradient Text
Headlines and brand elements use the teal → blue → purple gradient:
```css
.text-brand-gradient {
  background: linear-gradient(to right, #0d9488, #3b82f6, #8b5cf6);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}
```

### Animated Page Background
Page-level living gradient — One AI's tints, GBS's drift technique:
```css
body {
  background: linear-gradient(135deg, #f0f4f8 0%, #e6f0f0 50%, #eef2fb 100%);
  background-size: 400% 400%;
  animation: var(--animate-gradient-x);  /* 15s, slow, never distracting */
}
```

## Typography

- **Font:** Inter (300, 400, 500, 600, 700).
- **Scale:** use the `text-h1/h2/h3` tokens (size + line-height paired). Body `text-base` (15/24), small `text-sm` (13/20), caption `text-xs` (11/16).
- **Hierarchy:** `text-primary` for content, `text-secondary` for labels/supporting, `text-muted` for hints/captions/placeholders. Three tiers, no more.
- Headlines `font-semibold`/`font-bold`; body `font-normal`; UI labels `font-medium`.

## Spacing

4px base (Tailwind `1` = 4px). Reach for the scale, never ad-hoc px:
- `1–4` (4–16px): inline + tight padding · `6–8` (24–32px): card padding, section gaps · `10–12` (40–48px): page padding · `16–24` (64–96px): hero / vertical rhythm.

## Surfaces & Effects

### Glassmorphism
All panels, cards, modals, sidebars, the AI orb:
```css
.glass-panel {
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.5);
}
```
Tailwind: `bg-white/65 backdrop-blur-xl border border-white/50 rounded-xl shadow-sm`

### Primary Button — gradient + shimmer sweep + press feedback
A light sweep crosses the button continuously; it scales up on hover and presses in on click.
```css
.btn-primary {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 14px 32px; border: none; border-radius: 12px;
  background: linear-gradient(to right, #0d9488, #3b82f6); /* teal → blue */
  color: #fff; font-weight: 600; font-size: 15px;
  cursor: pointer; position: relative; overflow: hidden;
  transition: all 0.3s ease;
}
.btn-primary:hover  { transform: scale(1.02); box-shadow: 0 10px 25px -5px rgba(13, 148, 136, 0.3); }
.btn-primary:active { transform: scale(0.98); }
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none !important; box-shadow: none !important; }
.btn-primary::after {                 /* the shimmer sweep */
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(to right, transparent, rgba(255,255,255,0.2), transparent);
  transform: translateX(-100%); animation: var(--animate-shimmer);
}
.btn-primary:disabled::after { animation: none; }
```
Secondary: glass background, `text-primary`, border. Ghost: transparent, hover reveals glass. All buttons `transition-all duration-200`.

### Form Input — soft focus ring
```css
.form-input {
  width: 100%; padding: 12px 16px;
  border: 1.5px solid rgba(226, 232, 240, 0.7); border-radius: 10px;
  background: rgba(255, 255, 255, 0.7); color: var(--color-text-primary);
  font: inherit; outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.form-input::placeholder { color: var(--color-text-muted); }
.form-input:focus {
  border-color: #0d9488;                          /* One AI teal */
  box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.1);  /* soft teal ring */
}
```

## Animation Library

Each animation is bound to a One AI semantic — this is *why* it exists, not just decoration:

| Utility | Motion | One AI use |
|---------|--------|-----------|
| `animate-gradient-x` | background-position drift (15s) | Page background — the org "alive" |
| `animate-fade-in` | opacity 0→1 + translateY 10px→0 (0.8s) | **Everything entering** — content never pops |
| `animate-shimmer` | translateX -100%→100% (2.5s) | Skeleton loaders + button sweep |
| `animate-aura-pulse` | scale 1→1.3 + opacity 0.15→0.35 (3s) | **Personal-AI orb / presence glow** — the breathing organism |
| `animate-pulse-dot` | opacity 1→0.4 (2s) | Connector / agent status dots |
| `animate-clari-pulse` | expanding box-shadow ring (2s) | **Human-in-the-Loop** — "AI needs your approval" attention glow |
| `animate-bounce-soft` | translateY 0→-4px (0.6s) | AI **thinking / typing** indicator |
| `animate-slide-in-left` | translateX -100%→0 (0.3s) | Nexus module panels, drawers, sidebars |
| `animate-toast-in` | translateY 20px→0 + opacity (0.4s) | Proactive alerts / notifications |

```css
@keyframes gradient-x   { 0%,100% { background-position: left top; } 50% { background-position: right bottom; } }
@keyframes fade-in      { 0% { opacity: 0; transform: translateY(10px); } 100% { opacity: 1; transform: translateY(0); } }
@keyframes shimmer      { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
@keyframes aura-pulse   { 0%,100% { transform: scale(1);   opacity: 0.15; } 50% { transform: scale(1.3); opacity: 0.35; } }
@keyframes pulse-dot    { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
@keyframes bounce-soft  { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@keyframes slide-in-left{ from { transform: translateX(-100%); } to { transform: translateX(0); } }
@keyframes toast-in     { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
/* clari-pulse glow color = One AI purple (AI-action). Swap to teal rgba for success-adjacent prompts. */
@keyframes clari-pulse {
  0%,100% { box-shadow: 0 4px 16px rgba(139, 92, 246, 0.35); }
  50%     { box-shadow: 0 4px 24px rgba(139, 92, 246, 0.55), 0 0 0 8px rgba(139, 92, 246, 0.10); }
}
```

**The AI orb pattern:** absolutely-positioned blurred element behind the orb, filled with the aurora gradient, running `animate-aura-pulse` — gives the personal AI a living, breathing halo.

## Page Transitions — iOS-style directional push/pop (Framer Motion)

Screens slide like native iOS: forward navigation pushes the new screen in from the **right** while the old one parallaxes left and fades; "back" mirrors it. Every screen-level `motion.div` spreads one shared transition config.

```ts
// pageTransition.ts
import type { Transition } from "motion/react";
import { useLocation } from "react-router-dom";

const CURVE = { duration: 0.6, ease: [0.32, 0.72, 0, 1] } satisfies Transition; // Apple's ease-out

export const pageTransition     = { initial: { x: "100%" },  animate: { x: 0 }, exit: { x: "-30%", opacity: 0 }, transition: CURVE };
export const pageTransitionBack = { initial: { x: "-100%" }, animate: { x: 0 }, exit: { x: "30%",  opacity: 0 }, transition: CURVE };

// "back" navigations pass `navigate(target, { state: { nav: "back" } })`.
export function useDirectionalTransition() {
  const goingBack = (useLocation().state as { nav?: "back" } | null)?.nav === "back";
  return goingBack ? pageTransitionBack : pageTransition;
}
```
```tsx
// App.tsx — wrap routes
<AnimatePresence initial={false}>
  <Routes location={location} key={isLogin ? "login" : "app"}>…</Routes>
</AnimatePresence>

// each screen root
<motion.div {...useDirectionalTransition()} className="absolute inset-0 flex flex-col overflow-y-auto">
```
**Invariant:** transitioning screens must be `absolute inset-0` so incoming and outgoing pages overlap during the slide.

## Component Rules

- **Cards & Panels:** always glass — `bg-white/65 backdrop-blur-xl border border-white/50 rounded-xl`. Shadow `shadow-sm` (never heavy). Padding `p-6` standard / `p-4` compact. Enter with `animate-fade-in`.
- **Status indicators:** active/healthy → `animate-pulse-dot` teal dot · AI-action pending → `animate-clari-pulse` (purple) · syncing → shimmer · error → static red dot · inactive → muted gray.
- **AI presence:** orb / avatar carries an `animate-aura-pulse` aurora halo whenever the AI is "awake."
- **Scrollbars:** thin (6px), transparent track, `rgba(0,0,0,0.1)` thumb, `border-radius: 3px`.
- **Selection:** `selection:bg-brand-teal/20 selection:text-brand-teal`.

## Motion Principles

1. **Everything enters** — no content appears without at least `fade-in`.
2. **Every interactive responds** — hover scale-up, active press-in; touch is acknowledged.
3. **Ambient life is subtle** — the orb breathes, the background drifts, but motion never competes with content.
4. **Animate only `transform` and `opacity`** for 60fps. Never animate `width`/`height`/`top`/`left`. (`clari-pulse` uses `box-shadow` deliberately and sparingly.)
5. **Respect `prefers-reduced-motion`** — disable ambient loops and page slides; keep instant fades:
```css
@media (prefers-reduced-motion: reduce) {
  *, *::after { animation: none !important; transition-duration: 0.01ms !important; }
}
```

## What NOT to Do

- No heavy drop shadows (max `shadow-sm`).
- No solid opaque cards (always glass / near-transparent).
- No harsh borders (use `border-white/50` or softer).
- No static pages — everything fades or slides in.
- No spinners — use shimmer/skeleton loaders (exception: a tiny `animate-spin` for inline button busy-state is fine).
- No corporate blue-gray dashboards.
- No CSS-in-JS **styling** libraries (styled-components/emotion). Tailwind for style, Framer Motion for motion.
- No animating layout properties (width/height/top/left) — transform/opacity only.
- Never import GBS / Vetera / any other project's **brand colors** — One AI's aurora palette only.

## Reference Implementations

- **Colors (One AI aurora):** Vetera AI design system — `C:\Users\Yani_\Desktop\In-Progress\Vetera AI\MVP\UI\code-contract\` (`css/styles.css`, `index.html`).
- **Motion & surface vocabulary (this rule's source):** GBS pilot — `C:\Users\Yani_\Desktop\In-Progress\GBS\Project 2\Production\Prototype\`
  - `src/index.css` — Tailwind v4 `@theme` tokens, all keyframes, glass/button/input primitives.
  - `src/pageTransition.ts` — the iOS directional transition module.
  - `package.json` — `motion` (Framer Motion v12).
