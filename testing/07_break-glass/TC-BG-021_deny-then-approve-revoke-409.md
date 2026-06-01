<!--
  Test-case: TC-BG-021 — denied is terminal (AC4 state machine).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-021: Denied grant is terminal → approve 409, revoke 409

| Field | Value |
|---|---|
| **ID** | TC-BG-021 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | STATE — transition state-machine + concurrent row-lock |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify `denied` is a terminal state: once a company_admin denies a `requested` grant, it can be
neither approved nor revoked. PC-05-AC4 — approve/deny require `requested`; revoke requires
`requested|approved`. A denied grant satisfies neither, so both follow-ups must be **409**.

## Break hypothesis
The attacker's bet: a denied request is later resurrected. If approve only checks "not approved"
(rather than "is requested"), a denied grant could be approved → access granted **after an
explicit refusal**. Or revoke's `_REVOCABLE` set wrongly includes `denied`, letting a denied
grant transition further. A failure: approve→200 (denied grant becomes active) or revoke→200.

## Preconditions
- Live stack `:8000`. Fresh run-stamped org `state-021-<stamp>` + its company_admin.
- One fresh grant: platform requests (`requested`), company_admin denies it (200, terminal).

## Steps
1. Platform requests support access → grant `requested` (201).
2. Company_admin **denies** the grant → 200, `status='denied'`.
3. Company_admin **approves** the same denied grant → expect **409**.
4. Company_admin **revokes** the same denied grant → expect **409**.

## Expected result
- Deny: 200, `status='denied'`, `is_active=false`.
- Approve-after-deny: **409** ("Cannot decide a grant that is already denied.").
- Revoke-after-deny: **409** ("Cannot revoke a grant that is denied.").
- Status remains `denied` throughout (no field mutated by the rejected calls).

## Harness
Script: `harness/tc_021.py` · run: `docker compose exec -T backend python - < testing/07_break-glass/harness/tc_021.py` (prepend `_common.py`)

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Deny on a `requested` grant succeeded (200): `denied`, `is_active=false`. Both follow-ups on the
> denied grant were rejected: approve → **409** (`Cannot decide a grant that is already denied.`),
> revoke → **409** (`Cannot revoke a grant that is denied.`). `denied` is terminal in both the
> approve/deny guard (`_load_requested`) and the revoke guard (`_REVOCABLE` excludes `denied`).
> Final status stayed `denied`.

**Evidence**

```
ORG 362e6c73-f804-4cd6-a4a5-154689a5b706 state-021-19e846494450a8b
REQUEST 201 status= requested
DENY 200 status= denied is_active= False
APPROVE-AFTER-DENY 409 body= {'detail': 'Cannot decide a grant that is already denied.'}
REVOKE-AFTER-DENY 409 body= {'detail': 'Cannot revoke a grant that is denied.'}
FINAL status= denied is_active= False
ASSERT deny==200: True
ASSERT approve-after-deny==409: True
ASSERT revoke-after-deny==409: True
ASSERT final status denied: True
```

**Verdict**

The defense held. A refused (denied) request cannot be resurrected into access — the
break-hypothesis (denied grant re-approved or transitioned further) is refuted. Two guards
responsible: `company_support_service.py:134` (`_load_requested`, blocks approve/deny on
non-`requested`) and `company_support_service.py:121` (`grant.status not in _REVOCABLE`, where
`_REVOCABLE = {requested, approved}` at line 48 excludes `denied`). Confirms PC-05-AC4 holds live.

**Notes / follow-up**

`denied` is a true sink state — no outbound transition exists. Mirror case for the other sink:
TC-BG-022 (`revoked` terminal).
