# Platform Session (PR-2) — Adversarial Review & Resolutions

> **Scope:** PR-2 "platform session hardening" on branch `feat/platform-session` — backend
> `GET /platform/me` + `POST /platform/refresh` + `POST /platform/logout` (mirroring
> `/auth/*`), `PlatformAdminResponse` + `build_admin_view_by_id`; frontend domain-aware
> refresh/logout (platform refresh token now **in-memory**, never localStorage),
> `fetchCurrentPlatformAdmin`, and `AuthProvider` resolving the **real** admin identity via
> `/platform/me` (closing the AUD-14 synthesised-identity gap).
>
> **Method:** a security-weighted multi-agent Workflow ran 4 review lenses
> (security/privacy, correctness, test-integrity, code-quality); every finding was then
> adversarially verified against the real code. **4 confirmed, 1 dismissed.** Nothing was a
> functional vulnerability — the implementation's audience + subject_type confinement is
> correct; the high finding is a **test-confidence** defect.
>
> (First run aborted — all 4 agents were cut short before emitting findings due to too
> heavy a mandated read-list; re-run after trimming the workload succeeded.)

Post-fix gate: backend platform routes **16/16** (clean DB), frontend `tsc`/`eslint` ✓,
**82 tests** ✓, coverage 90.7%, `vite build` ✓.

## Confirmed findings & resolutions

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| test-1 | high | The two cross-domain negative tests (`/platform/me` + `/platform/refresh` with a company token) **did not discriminate the guard** — a company token's `sub` is a *user* id, so even with the audience/subject_type check deleted, the request still 401s via a *secondary* admin-not-found lookup. They'd stay green if the boundary were removed (the non-negotiable anti-pattern). | `/platform/me` test now mints the company token with a **real seeded platform admin's id** (so the audience guard is the only thing preventing a 200). `/platform/refresh` test now also asserts the company refresh token **still rotates at `/auth/refresh` afterwards** — proving the subject_type guard rejected *without* revoking (a removed guard would have consumed it). |
| test-2 | med | `AuthProvider.platformLogin` failure path (login OK, then `/platform/me` fails) was untested. | Added a LoginPage integration test: `/platform/me` → 500 ⇒ stays on login with a connectivity message, session torn down (`/platform/logout` called, storage cleared), no nav to console. |
| corr-1 | low | On a non-401 `/platform/me` failure, `platformLogin` left an orphaned in-memory platform token (status never flips, token unrevoked). | `platformLogin` now wraps `fetchCurrentPlatformAdmin()` in a `.catch` that runs `logoutRequest()` (clears in-memory + best-effort server revoke) before re-throwing. |
| cq-1 | low | Stale, self-contradictory Key-invariant docstring in `platformClient.ts` (said "closed in PR-2" while describing the pre-PR-2 behavior). | Rewritten to the true post-PR-2 behavior (domain-aware refresh; survives in-tab, not hard refresh — by design). |

## Dismissed (verified non-issue)

- **test-3** — claim that `fetchCurrentPlatformAdmin`'s 401→refresh path is untested.
  Refuted: that function is a 3-line delegation; the refresh routing lives in
  `performRefresh` (keyed on the in-memory token, **not** the URL), and
  `test_platform_session_refreshes_via_platform_endpoint` already proves it rotates via
  `/platform/refresh` (and not `/auth/refresh`). Covered.

## Notes carried forward

- **Live-verified** on the running stack: `/platform/me` (platform token) → 200 real
  identity; `/platform/refresh` → rotates; `/platform/me` (company token) → 401.
- **Pre-existing test-infra flake (not PR-2):** `tests/identity/conftest.py`'s
  `identity_schema` fixture truncates on **teardown only**, so the *first* test of a run
  collides (IntegrityError) when the dev DB already holds the demo seed. Re-running once
  (or running on a clean DB, as CI does) is green. Worth hardening the fixture to also
  truncate on **setup** so a pre-seeded dev DB can't pollute the first test — and it would
  retire the "re-seed after every backend test run" dance. Tracked here, not fixed in PR-2.
