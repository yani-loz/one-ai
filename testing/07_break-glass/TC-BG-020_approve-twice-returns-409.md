<!--
  Test-case: TC-BG-020 — approve a grant twice (AC4 state machine).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-020: Approve-twice on one grant → 409 (idempotency / state guard)

| Field | Value |
|---|---|
| **ID** | TC-BG-020 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | STATE — transition state-machine + concurrent row-lock |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the company approve transition is guarded by current state: a grant that is already
`approved` cannot be approved a second time. PC-05-AC4 — approve requires `requested`; a
re-approve must return **409 InvalidGrantTransitionError**, not silently re-stamp the time box.

## Break hypothesis
The attacker's bet: approve is a naive `status = 'approved'` write with no current-state check,
so a second approve succeeds (200) and **re-extends the 4h window** (a fresh `expires_at`) —
a privileged actor could keep a grant alive indefinitely by re-approving, defeating the time box.
A failure looks like the second approve returning 200 with a later `expires_at`.

## Preconditions
- Live stack `:8000`. Demo platform admin onboards a fresh run-stamped org (suite code `state`).
- One fresh org `state-020-<stamp>` + its company_admin (provisioned by `provision_company`).
- One fresh grant per case: platform requests (`requested`), company_admin approves once (200).

## Steps
1. Platform admin requests support access on the fresh org → grant `requested` (201).
2. Company_admin approves the grant → 200, `status='approved'`, `is_active=true`, `expires_at` set.
3. Company_admin approves the **same** grant again.
4. Assert the second approve → **409**; capture the first vs second `expires_at`.

## Expected result
- First approve: 200, `status='approved'`, `expires_at = t0 + 4h`.
- Second approve: **409** (`InvalidGrantTransitionError` — "Cannot decide a grant that is already approved.").
- Grant state unchanged by the rejected call (same `expires_at`, still `approved`).

## Harness
Script: `harness/tc_020.py` · run: `docker compose exec -T backend python - < testing/07_break-glass/harness/tc_020.py` (prepend `_common.py`)

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> First approve succeeded (200): `approved`, `is_active=true`, `expires_at = +4h`. The second
> approve on the same grant was rejected **409** (`Cannot decide a grant that is already approved.`).
> The rejected call mutated nothing — `expires_at` was identical before and after, so the time box
> was NOT re-extended. The state guard `_load_requested` (status must be `requested`) holds.

**Evidence**

```
ORG e6bb79eb-ffd6-4bce-b8a8-2dd5742bb383 state-020-19e84648514ce3a
REQUEST 201 status= requested
APPROVE#1 200 status= approved is_active= True expires_at= 2026-06-01T22:14:01.412707Z
APPROVE#2 409 body= {'detail': 'Cannot decide a grant that is already approved.'}
FINAL status= approved is_active= True expires_at= 2026-06-01T22:14:01.412707Z
ASSERT approve#1==200: True
ASSERT approve#2==409: True
ASSERT expires_at unchanged: True
```

**Verdict**

The defense held. A second approve cannot re-stamp the time box — the break-hypothesis (window
re-extension via re-approve) is refuted. Guard responsible:
`company_support_service.py:134` (`_load_requested` raises `InvalidGrantTransitionError` when
`grant.status != requested`), surfaced as 409 via the error handler. Confirms PC-05-AC4
(`::test_approve_twice_returns_409`) holds live, not just in the unit suite.

**Notes / follow-up**

Idempotency-relevant: the guard makes approve non-idempotent on purpose (409, not a no-op 200) so
the audit trail can't gain a phantom second `support.approved` event. Related: TC-BG-023 (deny
shares this guard).
