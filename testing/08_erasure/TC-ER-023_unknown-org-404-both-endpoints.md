# TC-ER-023: Unknown org → 404 on both endpoints (existence not revealed)

| Field | Value |
|---|---|
| **ID** | TC-ER-023 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | RETAIN — append-only audit retained + compliance export |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove both PC-06 endpoints return 404 for a non-existent org: `erase_org(random uuid4)` → 404
and `compliance-export(random uuid4)` → 404. The erase 404 fires from the row-lock lookup
(`get_for_update` → None) BEFORE the slug check, so nothing is ever touched.

## Break hypothesis
The attacker's bet: erase against an unknown uuid does something other than a clean 404 — a 500
(unhandled None), a 400 (slug check reached first, leaking ordering), or a 200 (phantom erase).
Likewise the export returns a non-404. REFUTES-FIX / NEW = any non-404 on either endpoint.

## Preconditions
- Live stack `:8000`; harness inside the backend container over stdin.
- Two fresh `uuid4()` values that are NOT real orgs. No org is provisioned or mutated — this
  case erases NOTHING real.
- Platform auth via canonical `/platform/login`; the erase carries a valid `password` so the
  request VALIDATES and the existence check (404 path) is actually reached.

## Steps
1. Platform login.
2. `erase_org(uuid4(), confirm_slug="does-not-matter", password=PLATFORM_PW)` → expect 404.
3. `compliance_export(uuid4())` → expect 404.
4. Assert both are 404 with a generic detail (no existence oracle).

## Expected result
- Erase unknown org → 404 (`get_for_update` → None → `OrganizationNotFoundError`), BEFORE the
  slug check; nothing touched.
- Export unknown org → 404 (`get_with_user_count` → None → `OrganizationNotFoundError`).

## Harness
Script: `harness/tc_023.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_023.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:44 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> `erase_org(<random uuid4>, confirm_slug="does-not-matter", password=PLATFORM_PW)` returned 404
> with `{"detail": "Organization not found."}` — the existence check (`get_for_update` → None)
> fired before the slug check, so the slug mismatch never surfaced and nothing was touched.
> `compliance_export(<random uuid4>)` returned 404 with `{"detail": "Organization not found."}`.
> Both endpoints reject an unknown org cleanly with a generic detail.

**Evidence**

```
=== TC-ER-023 ===
GHOST_ERASE_ID: bb34ea0f-43c9-4103-b0ad-1deaacfa167d
GHOST_EXPORT_ID: 1bb7d068-7725-46a4-afd2-8653d4c15fec
ERASE_UNKNOWN: 404 body: {'detail': 'Organization not found.'}
EXPORT_UNKNOWN: 404 body: {'detail': 'Organization not found.'}
RESULT: PASS
```

**Verdict**

The defense held. Erase loads the org `FOR UPDATE` first and raises `OrganizationNotFoundError`
on None (`erasure_service.py:91-93`) — ordered BEFORE the slug check (line 94) — so an unknown
org can never reach the destructive path or leak via a slug-specific error. Export mirrors this
via `get_with_user_count` → None → 404 (`erasure_service.py:146-148`). Both map to a 404 with a
generic detail (no existence oracle). Confirms the review-added `test_erase_unknown_org_returns_404`
+ `test_compliance_export_unknown_org_returns_404`.

**Notes / follow-up**

None — clean negative path. The error→404 mapping lives in `exceptions.py` / `error_handlers.py`
(`OrganizationNotFoundError`).

---

## Addendum — re-auth password gate corroboration (running binary)

The running `ErasureRequest` requires a re-auth `password` field absent from the reviewed
on-disk source (3 fields live vs. 2 in `schemas/erasure_schemas.py` / the PR-6 review / the task
facts). A negative probe with a WRONG (non-empty) password, against a fresh own org, confirms the
gate is substantive, not cosmetic:

```
ORG: 2b169e17-148a-49cf-a032-897fbb2d3b67 retain-pwgate-19e84869ec010bb
ERASE_WRONG_PW: 403 {"detail":"Password confirmation failed."}
ORG_STATUS_AFTER_WRONG_PW: 200 active user_count: 1
```

Wrong password → **403 "Password confirmation failed."**, org UNTOUCHED (still `active`,
user_count 1, nothing deleted). The control genuinely re-authenticates the platform admin before
an irreversible erase — incidental hardening beyond the documented design. Not a defect; a
defense that held. (Discrepancy: the running binary enforces a control not present in the
reviewed source — material for the QA record, tagged as a documented-binary-vs-source note.)
