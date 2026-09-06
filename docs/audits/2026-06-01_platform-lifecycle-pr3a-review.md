# Org Lifecycle (PR-3a) — Adversarial Review & Resolutions

> **Status as of 2026-09-06 (dated record — the findings and resolutions below are unchanged and still verify in code):** both items under "Notes carried forward" are **still open follow-ups**. (1) The closing *"a frontend review is recommended as a separate pass"* for the PC-03a org-detail screen (`/platform/orgs/:id`) has never been run — `docs/audits/` holds exactly one frontend Playwright pass, `2026-06-01_frontend-platform-console-dynamic.md`, whose scope is the PC-01 console list plus the identity auth client, not the detail screen. (2) The test-split follow-up is also open: `backend/tests/identity/routes/test_platform_routes.py` is **484 lines** today — still under the 500-line hard ceiling, still one file.

> **Scope:** PR-3a "org lifecycle" backend on branch `feat/platform-lifecycle` — the
> `OrganizationStatus` enum + CHECK, `legal_hold` (migration `0004`), the new
> `PlatformOrgService` + `GET /platform/orgs/{id}` / `PATCH …/status` / `PATCH …/legal-hold`,
> and **suspend-blocks-login** (login + refresh, gated after the credential check).
>
> **Method:** a security-weighted multi-agent Workflow ran 4 review lenses (security,
> correctness, tests, code-quality); every finding was adversarially verified.
> **7 confirmed, 0 dismissed — and zero functional or security defects.** The
> implementation is sound: suspend-blocks-login is correctly *not* an enumeration oracle,
> audience confinement holds, and the detail endpoint is content-blind. All 7 findings are
> test-strength / docstring / naming.

Post-fix gate: backend **42 route tests + 122 identity tests** green (clean DB); ruff clean;
migration `0004` applies.

## Confirmed findings & resolutions

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| test-1 | med | PATCH status/legal-hold tests asserted only the in-memory response body — never read back persistence; no test linked PATCH → the auth gate end-to-end. | Added a **read-back GET** after the legal-hold PATCH, and an **end-to-end test** (`test_patch_status_endpoint_drives_the_login_gate_end_to_end`): suspend via the endpoint → company login 403 → reactivate → login 200. |
| sec-1 | low | `_load_active_org` only rejects *suspended* but the name implies it guarantees *active*. | Renamed `_load_loginable_org`; docstring clarifies onboarding/offboarded are allowed (offboarded cutoff is PC-06). |
| test-2 | low | No test pinning the deliberate asymmetry (`/auth/me` still 200 under suspension). | Added `test_me_with_valid_token_succeeds_even_when_org_suspended`. |
| cq-1 | low | `platform_routes.py` "Depends on" docstring stale (omitted `platform_org_service`). | Updated. |
| sec-2 | nit | No company-token-rejection test on `PATCH legal-hold` (the most safety-critical flag). | Added `test_patch_org_legal_hold_with_company_token_is_rejected` (discriminating). |
| test-3 | nit | Cross-domain tests asserted `in (401, 403)` — the contract is exactly 401. | Tightened all platform-route rejection asserts to `== 401`. |
| cq-2 | nit | `organization_repository.py` "Used by" docstring omitted the new `PlatformOrgService` consumer. | Updated. |

## Notes carried forward

- **Dev-seed hygiene (related, fixed alongside):** the demo orgs previously used fixed
  reserved UUIDs (`…0001`/`…0002`), which *looked* enumerable in dev URLs. `seed_identity.py`
  now lets the DB generate **random** UUIDs (like real onboarding), so dev mirrors prod — no
  guessable org ids anywhere. (Real onboarded orgs were always random; the seed is
  prod-guarded, so this was never a production exposure.)
- **Follow-up:** `test_platform_routes.py` is at 468 lines — split into auth-session vs
  org-management test files soon (soft-warn; under the 500 hard ceiling).
- Frontend detail screen (`/platform/orgs/:id`) shipped alongside; a frontend review is
  recommended as a separate pass.
