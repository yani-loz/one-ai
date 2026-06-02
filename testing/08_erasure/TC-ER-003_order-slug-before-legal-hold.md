<!--
  TC-ER-003 — guard order: slug (400) precedes legal-hold (409). Suite HOLD.
-->

# TC-ER-003: Guard order — slug check (400) fires before the legal-hold check (409)

| Field | Value |
|---|---|
| **ID** | TC-ER-003 |
| **Target** | 08 — GDPR erasure + compliance export (PC-06) |
| **Suite** | HOLD — legal-hold-beats-erasure + slug guard |
| **Type** | Negative / Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Confirm the documented guard ORDER: when BOTH a wrong slug AND a legal hold apply, the slug
check (400) wins — it fires before the legal-hold check (409). This proves the slug-confirm
guard is the outermost destruction gate and nothing is touched regardless of hold state.

## Break hypothesis
The legal-hold check runs before the slug check, so a held org with a wrong slug returns 409
instead of 400 — revealing a different ordering than documented, or worse, the deletes run
before either guard. Any 409 (instead of 400), or a 200/5xx, or any data change, is the
finding.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `O` provisioned this run (slug `hold-er3-<stamp>`).
- Org placed under legal hold (patch verified to have taken: 200 + `legal_hold == true`).
- Note: the LIVE order is lock → slug(400) → password(403) → legal-hold(409); this case sends a
  VALID password and a WRONG slug, so the slug 400 must fire first regardless of the password or
  the hold.

## Steps
1. Provision org `O`; `patch_legal_hold(O, true)`; assert it took.
2. psql baseline: users, status, `org.erased` rows.
3. `erase_org(O, confirm_slug='wrong-<stamp>')` with a valid password → expect **400** (slug
   precedes legal-hold), NOT 409.
4. psql after: users intact, status `active` (not offboarded), no `org.erased` row.
5. Clear the hold.

## Expected result
- Erase → HTTP **400** (slug mismatch), never 409. `ErasureConfirmationError` precedes
  `LegalHoldError`.
- psql: users unchanged, status `active`, no `org.erased` row.

## Harness
Script: `harness/tc_003.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_003.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> With a legal hold in force AND a wrong `confirm_slug`, erase returned **400**
> `{"detail":"Confirmation does not match the organization's slug."}` — the slug guard fired
> first, the legal-hold 409 never reached. The org was untouched: 1 user, status `active`, no
> `org.erased` row.

**Evidence**

```
patch_legal_hold(true): 200 | legal_hold now: True
BASELINE (held): {'users': 1, 'status': 'active', 'erased_rows': 0}
ERASE (wrong slug + held): 400 {"detail":"Confirmation does not match the organization's slug."}
AFTER: {'users': 1, 'status': 'active', 'erased_rows': 0}
RESULT 400(not 409): True | nothing_touched: True
VERDICT: PASS
cleanup patch_legal_hold(false): 200 | ORG_ID: d44495f7-7185-4266-a5a2-dc9b8e7c5fc8 SLUG: hold-er3-19e847810963ba0
```

> Both a legal hold AND a wrong slug applied; the response was 400 (slug), never 409 (hold) —
> the slug guard is the outermost gate.

**Verdict**

The defense held and the documented ORDER is confirmed. `erase_organization` checks the slug
(`ErasureConfirmationError`, 400) before the legal-hold guard (`LegalHoldError`, 409) — so the
slug-confirm is the outermost destruction gate. CONFIRMS-FIXED for the ordering invariant. (Live
order is lock → slug 400 → password 403 → legal-hold 409; the slug still wins.)

**Notes / follow-up**

Hold cleared after the proof; org `O` left intact. Complements TC-ER-001 (hold→409 with a
correct slug) and TC-ER-002 (wrong slug→400 with no hold).
