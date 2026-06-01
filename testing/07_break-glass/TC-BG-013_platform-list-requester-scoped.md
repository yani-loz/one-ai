<!--
  Test-case: TC-BG-013 — platform list is requester-scoped (AC5, list side).
-->

# TC-BG-013: `GET /platform/support-requests` is requester-scoped (forged admin's grant absent)

| Field | Value |
|---|---|
| **ID** | TC-BG-013 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | ISO — cross-tenant isolation + requester-scoping |
| **Type** | Negative / Adversarial (requester scoping) |
| **Severity if it fails** | High (one platform admin sees every admin's requests) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`GET /platform/support-requests` returns only the calling platform admin's OWN requests. A
grant requested by a SECOND platform identity (a forged platform token with a random `sub`)
must be ABSENT from the DEMO platform admin's list — and PRESENT in the forging admin's own
list (positive control). Proves PC-05-AC5 (list side) / review finding #2.

## Break hypothesis
If `list_for_requester` dropped its `WHERE requested_by_admin_id = :me` filter, every
platform admin would see every other admin's break-glass requests (which tenants are under
investigation, the reasons) — a privacy + operational-intelligence leak across Ethera staff.
A negative-only test would not catch a bug returning `[]` for everyone; the POSITIVE control
(the forging admin DOES see its own grant) is what makes this falsifiable.

## Preconditions
- Live stack `:8000`. Demo platform admin logged in.
- One fresh run-stamped org A (prefix `iso-013-a`) to target — never a demo/globex org.
- A SECOND platform token forged via `forge_platform_token()` (random `sub`, dev secret).
  This is a real capability of the forgeable dev secret; `request_access` does
  `get_by_id(random_sub) → None`, storing `requested_by_email=None`,
  `requested_by_admin_id = <random sub>` (the table has no FK) — expected, not a defect.

## Steps
1. Forge platform token `F` with a random `sub`.
2. As `F`, request break-glass access to A → `201`, grant `G`,
   `requested_by_admin_id == F.sub`, `requested_by_email == None`.
3. Positive control — as `F`, `GET /platform/support-requests` → `G` MUST be present.
4. Negative — as the DEMO platform admin, `GET /platform/support-requests` → `G` MUST be
   ABSENT (asserted by grant id, never by count — the demo admin's list accumulates grants
   across runs).

## Expected result
- Request as `F`: `201`, `requested_by_admin_id == F.sub`, `requested_by_email == None`.
- `F`'s list: `200`, contains `G`.
- Demo admin's list: `200`, does NOT contain `G`.

## Harness
Script: `harness/tc_013.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_013.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 18:18 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged platform admin sees its own grant in its list (positive control), and that grant
> is absent from the demo platform admin's list. The list is filtered by
> `requested_by_admin_id`. The forged request stored `requested_by_email=None` as expected
> (no FK; random sub resolves to no admin row).

**Evidence**

```
forged sub=6c4e5fe1-ed0d-45f8-a421-11b80614790a
request as F -> status=201 grant_id=ac870ec5-e053-4278-aadc-bb584ce8a5a3 requested_by_admin_id=6c4e5fe1... requested_by_email=None
F list -> status=200 grant_in_F_list=True F_count=1          (positive control)
demo-admin list -> status=200 grant_in_demo_list=False demo_count=76
ASSERT present_for_F=True AND absent_for_demo=True -> PASS

# Note demo_count=76 (grants accumulated across runs on the shared/persistent DB) — this is
# exactly why the assertion is by grant_id, never by count. The forged grant is the only row
# in F's list and is NOT among the demo admin's 76 rows.
# psql attribution: support_grant.requested_by_admin_id = 6c4e5fe1...(F), requested_by_email NULL
# (random sub -> no platform_admin row -> denormalized email is None; no FK on the table).
```

**Verdict**

Defense held. The list is requester-scoped: each platform admin sees only the grants they
requested. The positive control (F sees G) rules out a vacuous-`[]` false pass; the negative
(demo admin does NOT see G) proves the filter. Confirms PC-05-AC5 + review finding #2
(`test_list_my_requests_is_requester_scoped`). Code path: `routes/support_routes.py:61-69`
→ `services/platform_support_service.py:84-87` (`list_my_requests` passes `actor.subject_id`)
→ `repositories/support_grant_repository.py:74-81`
(`SELECT … WHERE requested_by_admin_id = :me`).

**Notes / follow-up**

The forged-token-can-request behavior is the documented dev-secret blast radius (Rotate
JWT_SECRET in FIX_BEFORE_PROD), NOT the subject of this case — requester-scoping itself is a
fixed control that holds, so the tag is CONFIRMS-FIXED. Companion to TC-BG-014 (revoke side).
