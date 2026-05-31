# TC-IA-029: Malformed claims (non-UUID sub / org_id) → 401, not 500

| Field | Value |
|---|---|
| **ID** | TC-IA-029 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authorization / token validation |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify that a token which passes signature + audience + required-claim checks but carries a
**non-UUID** `sub` or `org_id` is rejected as **401 TokenInvalid**, not crashed into a 500
— i.e. the `_principal_from_claims` `try/except (KeyError, ValueError)` actually catches the
`UUID(...)` parse failure.

## Break hypothesis
The attacker's bet: claim values are trusted post-decode, so `UUID("not-a-uuid")` raises an
uncaught `ValueError` that bubbles past the identity handlers to FastAPI's default → an
opaque **500** (an error-handling/robustness defect, and a faint info-leak surface). A 500
on either variant is the finding.

## Preconditions
- Live stack. Two tokens forged with the real dev secret and `aud='company'` (so signature
  and audience pass), each with exactly ONE non-parseable field:
  - Variant A: `sub='not-a-uuid'`, `org_id` = valid UUID.
  - Variant B: `org_id='not-a-uuid'`, `sub` = valid UUID.

## Steps
1. Variant A token → `GET /auth/me` and `GET /users`.
2. Variant B token → `GET /auth/me` and `GET /users`.
3. Inspect the **body** of each response — a 401 with the malformed-claims detail is the
   pass; a 500 + traceback is the finding.

## Expected result
- All four → **401** `{"detail":"Access token claims are malformed."}` — `_principal_from_claims`
  catches the `ValueError` from `UUID(...)` and raises `TokenInvalidError`. No 500.

## Harness
Script: `harness/tc_029.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_029.py`

---

## Execution result

- **Run at:** 2026-05-31 08:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both the non-UUID `sub` and the non-UUID `org_id` tokens returned **401** with the
> dedicated message `{"detail":"Access token claims are malformed."}` on `/auth/me` and
> `/users`. No 500 and no traceback in any of the four responses.

**Evidence**

```
[attack] GET /auth/me (sub='not-a-uuid')    -> 401 {"detail":"Access token claims are malformed."}
[attack] GET /users   (sub='not-a-uuid')    -> 401 {"detail":"Access token claims are malformed."}
[attack] GET /auth/me (org_id='not-a-uuid') -> 401 {"detail":"Access token claims are malformed."}
[attack] GET /users   (org_id='not-a-uuid') -> 401 {"detail":"Access token claims are malformed."}
```

**Verdict**

Defense **held**. `_principal_from_claims` (`dependencies.py:60-66`) wraps the
`UUID(str(claims["sub"]))` and `UUID(str(raw_org))` parses in `try/except (KeyError,
ValueError)` and re-raises `TokenInvalidError` → 401 (`error_handlers.py:38`). The distinct
"claims are malformed" message (vs the generic "invalid") proves it is this guard, not the
decoder, that handled the input. No 500 path; confirms the pre-flagged CONFIRMS-FIXED
hypothesis. The dedicated message reveals only "your claims were malformed" — no internal
state or stack leaks.

**Notes / follow-up**

The isolation design (one bad field per token, the other valid) confirms *both* parse sites
are guarded, not just `sub`. Error-path hygiene (no stack/secret leakage) aligns with the
infrastructure error-hygiene cases (TC-IA-006).
