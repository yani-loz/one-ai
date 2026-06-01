<!--
  XDOM suite — cross-domain confinement + forged-token blast radius on the new write
  endpoints. See ../README.md (testing/README.md) for legend/tags.
-->

# TC-OL-021: Company-aud token (real admin sub) rejected on `PATCH …/status` — 401 + no write

| Field | Value |
|---|---|
| **ID** | TC-OL-021 |
| **Target** | Org Lifecycle (PC-03a) — `PATCH /platform/orgs/{id}/status` |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A company-audience token (with a REAL platform-admin `sub`) must be rejected (401) on the
suspend/reactivate write endpoint, AND the rejected PATCH must have **no effect** — the org's
status stays `active` (confirmed via an independent real-platform read-back). The company domain
cannot reach the lifecycle write surface, and a rejected request cannot mutate state.

## Break hypothesis
If the audience guard were missing, the forged company token (valid dev-secret signature, real
admin sub) would reach `set_status`, flip the org to `suspended`, and the `get_session`
dependency would COMMIT it — silently suspending a customer from the company side. The defense
bet is that `get_current_platform_admin` rejects the token at the dependency layer, *before* the
route/service runs, so `set_status` never executes and nothing is committed. The read-back GET is
the empirical no-write proof (not just the 401 status).

## Preconditions
- Live stack up; demo platform admin exists/active; real admin id fetched at runtime.
- A run-stamped, freshly-provisioned target org (status `active`, legal_hold `false`) via
  `provision_company(c, plat, "xdom")` — it is OURS, safe to target.
- `forge_company_token(sub=<real admin id>, org_id=None)` → hostile company-aud token.
- The PATCH uses the `patch_status` helper (valid body `{"status":"suspended"}`), so a 401 is
  unambiguously the audience gate — never a 422 from body validation.

## Steps
1. `platform_login` + `GET /platform/me` → real admin id; `provision_company` → target org.
2. Forge a company-aud token with `sub=<real admin id>`.
3. `PATCH /platform/orgs/{id}/status {"status":"suspended"}` with the forged company token.
4. `GET /platform/orgs/{id}` with the REAL platform token → read back `status`.

## Expected result
- Step 3: `401 {"detail":"Access token is invalid."}` (audience mismatch).
- Step 4: `status == "active"` — UNCHANGED. The rejected PATCH committed nothing.

## Harness
Script: `harness/tc_021.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_021.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged company-aud token (real admin sub, valid dev-secret signature) was rejected at
> `PATCH /platform/orgs/{id}/status` with `401 {"detail":"Access token is invalid."}`. The
> independent read-back with the real platform token shows the org is still `active` — the
> rejected PATCH committed nothing. psql ground-truth corroborates (`status=active` at the DB).

**Evidence**

```
== TC-OL-021 — company-aud token (real admin sub) -> PATCH status, 401 + NO write ==
[setup]   target org: xdom-19e8355db85e9cd (5c1673d8-78d8-4781-ab6b-877b64eb1f0b) status_before=active
[attack]  PATCH /status {suspended} (FORGED company-aud token): 401
          body: {'detail': 'Access token is invalid.'}
[readback] GET detail (real platform token): 200 status_after=active
RESULT: PASS — 401 at auth dependency; status unchanged (active) => no write

-- psql ground-truth --
         slug         | status
----------------------+--------
 xdom-19e8355db85e9cd | active
(1 row)
```

**Verdict**

The defense held in both dimensions. The 401 fires at the route's `get_current_platform_admin`
dependency (`backend/app/identity/routes/platform_routes.py:147` →
`dependencies.py:117`/`security/tokens.py:77-83`, audience mismatch) *before* the
`update_organization_status` handler body or `PlatformOrgService.set_status`
(`services/platform_org_service.py:43`) ever runs — so no ORM attribute is set and the
`get_session` unit-of-work never reaches its commit. The body sent was a valid
`{"status":"suspended"}` (via the `patch_status` helper), so the 401 is unambiguously the
audience gate, not Pydantic body validation. The read-back GET (and the psql snapshot) are the
empirical no-write proof. CONFIRMS-FIXED (PC-03a-AC6, write-endpoint + no-side-effect).

**Notes / follow-up**

Target org left active and untouched (this case caused no net change). Twin of TC-OL-020 (GET
detail) and TC-OL-022 (legal-hold). Contrast TC-OL-024, where a *platform*-aud forged token —
which passes the audience gate — DOES suspend the org, isolating audience (not signature) as the
sole control the company side fails.
