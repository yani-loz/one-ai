# Platform Console (PR-1) — Adversarial Review & Resolutions

> **Scope:** the new `frontend/src/platform/` module (the super-admin "governance control
> plane" fleet view + onboard drawer) on branch `feat/platform-console`, plus its wiring
> (`App.tsx` RoleHome + `/platform` route, `LoginPage` role-aware navigation, the
> `authorizedFetch` barrel re-export, the `.vite` eslint ignore).
>
> **Method:** a multi-agent Workflow ran 5 independent review lenses (security/privacy,
> correctness/wiring, design+a11y, test integrity, code-quality/SOLID); **every** finding
> was then handed to an adversarial verifier that confirmed or refuted it against the real
> code. 29 agents, ~1.37M tokens. Result: **22 confirmed, 2 dismissed**. Nothing was rated
> critical or high (the verifiers correctly capped the design findings at medium).

All 22 confirmed findings were fixed in this PR; the 2 dismissals were verified as
non-issues. Post-fix gate: `tsc` ✓ · `eslint` ✓ · **79/79 tests** ✓ · **coverage 91.1%**
✓ · `vite build` ✓ · file-size ✓ (no new warnings).

## Confirmed findings & resolutions

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| sec-1 | med | Platform login persisted the real 7-day platform refresh token to `localStorage` (XSS-exfil exposure; a takeover primitive once `/platform/refresh` lands in PR-2). | `setTokens(tokens, persistRefresh=false)`; `platformLogin` no longer persists the refresh token — platform session is in-memory only. Test flipped to assert it is **not** stored. |
| corr-1 | med | Admin email never gated client-side; a 422 mis-mapped to a connectivity error. | Email added to the form guard; generic message reworded. |
| corr-2 | med | Onboard submit didn't handle a 401 like the list does (stranded the operator). | New `onSessionExpired` prop; drawer mirrors the list's 401→logout. |
| dl-1 | med | `StatusBadge` used non-aurora amber. | `suspended` now uses `brand-red` (on-palette). |
| dl-2 | med | Drawer slide ignored `prefers-reduced-motion` (Framer bypasses the CSS block). | Slide gated on `useReducedMotion()` → instant fade when reduced. |
| dl-3 | med | Drawer claimed `aria-modal` with no focus trap / Escape / focus restore. | New `useDialogA11y` hook: focus-in, Tab trap, Escape-to-close, focus restore. |
| dl-4 | med | Status-filter chips conveyed selection by color only. | `aria-pressed` added to each chip. |
| dl-5 | low | Scrim used raw `slate-900`. | Now `bg-text-primary/20` (token). |
| dl-6 | nit | Inactive dots used raw `gray-400`. | Now `bg-text-muted/60` (token). |
| corr/test ×11 | med→nit | Coverage gaps: RoleHome platform branch, slug-stop-override, invalid bounds, generic error branch, close-without-onboard, search-no-match, copy toggle, unknown-status fallback, invalid-date fallback, 401 client path, plus stale/incomplete docstrings (`authClient` "only caller", `types.ts` Used-by, `platformClient` test doc) and the generic `isValid` name. | Added/over­hauled tests across `App`, `OnboardCompanyDrawer`, `PlatformConsolePage`, `platformClient`, plus new `StatusBadge`, `CompanyCard`, `useDialogA11y`, `onboardValidation` test files; docstrings corrected; `isValid`→`isOnboardFormValid` (extracted with `slugify` to `onboardValidation.ts`). |

## Dismissed (verified non-issues)

- **sec-2** — claim that docstrings *assert* an in-memory-only invariant and thereby mask
  sec-1. Refuted: `authClient` explicitly documents "refresh in localStorage", and the
  exposure is already tracked in `FIX_BEFORE_PROD.md` — nothing is masked. (The real
  exposure lives in sec-1 and is fixed.)
- **corr-3** — claim that `resetForm` before the `AnimatePresence` exit flashes an empty
  form during slide-out. Refuted: AnimatePresence renders the *stored* (success) subtree
  during exit, and React batches `resetForm`+`onClose`, so no blank-form frame occurs.

## Notes carried forward

- **AUD-14** (platform session in-memory / no `/platform/refresh|me`) remains the accepted
  deferral — closed by **PR-2**. sec-1's fix means that when `/platform/refresh` lands, the
  platform refresh token must be held **in memory**, never re-persisted.
