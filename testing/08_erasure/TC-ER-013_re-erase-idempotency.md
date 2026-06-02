# TC-ER-013: Re-erase idempotency (already-offboarded org)

| Field | Value |
|---|---|
| **ID** | TC-ER-013 · **Target** Erasure (PC-06) · **Suite** ERASE |
| **Type** | Negative · **Severity if fail** Medium · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-FIXED |

## Objective
Erasing an already-offboarded org is safe and idempotent — no 5xx, no corruption, zero new deletions.

## Steps / Harness
Provision E4; `erase_org(E4)` (200); `erase_org(E4)` again (same slug+password). `harness/tc_013.py`.

## Execution result
- **Run at:** 2026-06-01 · **Result:** ✅ Pass · **Tag:** CONFIRMS-FIXED

**Evidence**
```
ERASE #1 200 users=1 tokens=1 | ERASE #2 200 users=0 tokens=0 scrubbed=0 status=offboarded
psql: status=offboarded surviving_users=0
```

**Verdict**
Defense held. Set-based deletes return rowcount 0 over the now-empty stores; re-writing `status=offboarded`
is harmless; no 5xx. Re-erase is a safe no-op (corroborates PR-6 `test_re_erase_is_idempotent`). PC-06 confirmed.
