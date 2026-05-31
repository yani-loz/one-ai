# TC-IA-062: Password length boundaries (72 ok, 8 ok, 7 too short)

| Field | Value |
|---|---|
| **ID** | TC-IA-062 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Boundary |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Pin the inclusive boundaries of `BcryptPassword` on `POST /users`:
- exactly **72 ASCII bytes** → `201` (upper byte-bound inclusive, hashes fine),
- exactly **8 chars** → `201` (lower `min_length` inclusive),
- exactly **7 chars** → `422` (`min_length` violated).
No `500` on any boundary.

## Break hypothesis
An off-by-one in the byte guard would reject 72 bytes (`<` vs `<=`) or accept 73; an
off-by-one in `min_length` would accept 7 chars or reject 8. Either is a boundary
defect; a 72-byte 500 would also re-open AUD-02.

## Preconditions
- Live stack; namespace `iv-062-*`; fresh org + admin.

## Steps
1. Onboard fresh org + admin; company-login.
2. `POST /users` ×3: 72-byte ASCII pw, 8-char pw, 7-char pw (unique emails).
3. Record status per case vs expected.

## Expected result
`201`, `201`, `422` respectively. The `422` body is a `string_too_short` error
(`min_length=8`). No `500`.

## Harness
Script: `harness/tc_062.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_062.py`

---

## Execution result

- **Run at:** 2026-05-31 12:01 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> All three boundaries behaved exactly per contract: 72-byte and 8-char passwords were
> accepted (`201`); the 7-char password was rejected (`422`, `string_too_short`). No 500.

**Evidence**

```
[onboard] status=201
[72-byte-ASCII] pw_chars=72 pw_bytes=72 status=201 expected=201 match=True
[8-char-min] pw_chars=8 pw_bytes=8 status=201 expected=201 match=True
[7-char-too-short] pw_chars=7 pw_bytes=7 status=422 expected=422 match=True
[7-char-too-short] body={"detail":[{"type":"string_too_short","loc":["body","password"],"msg":"String should have at least 8 characters","input":"Abc1234","ctx":{"min_length":8}}]}
```

**Verdict**

Defense **held**. The 72-byte upper bound is inclusive (`> _BCRYPT_MAX_PASSWORD_BYTES`
in `user_schemas.py:34` rejects only 73+), and `Field(min_length=8)` rejects 7 while
admitting 8. Boundaries are correctly placed; no off-by-one, no 500. Positive/boundary
control test — no prior-audit linkage.

**Notes / follow-up**

Confirms the inclusive edges around the guards exercised by TC-IA-060/061.
