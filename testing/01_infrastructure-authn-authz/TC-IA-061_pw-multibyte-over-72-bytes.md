# TC-IA-061: Multibyte password >72 bytes / <72 chars → 422

| Field | Value |
|---|---|
| **ID** | TC-IA-061 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the byte-limit guard bounds **bytes, not characters**: a password under 72
CHARACTERS but over 72 BYTES (30 emoji ≈ 120 bytes) is rejected with `422`, not `500`,
and creates no user. This is the multibyte axis of the AUD-02 fix that a naive
`max_length` (character) bound would miss.

## Break hypothesis
If the guard checked character count instead of UTF-8 byte length, a 30-char / 120-byte
emoji password slips past validation, reaches `bcrypt.hashpw`, and triggers the bcrypt
5.x `ValueError` → unmapped → **HTTP 500** with no account. A 500 here REFUTES the fix.

## Preconditions
- Live stack; namespace `iv-061-*`; fresh run-stamped org + admin.

## Steps
1. Onboard a fresh org + admin; company-login for the token.
2. `POST /users` with `password = "😀" * 30` (30 chars, 120 UTF-8 bytes).
3. `GET /users`; assert victim absent.

## Expected result
`422` Pydantic value-error on `body.password` ("password must be at most 72 bytes"). No
`500`. Victim not listed.

## Harness
Script: `harness/tc_061.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_061.py`

---

## Execution result

- **Run at:** 2026-05-31 12:01 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED (AUD-02, multibyte axis)

**Actual behavior**

> A 30-character / 120-byte emoji password (well under the 72-char `max_length`, well
> over the 72-byte bcrypt limit) was rejected with `422`. No `500`, no user created.

**Evidence**

```
[onboard] status=201
[probe] password: chars=30 bytes=120 (chars<72, bytes>72)
[create_user multibyte>72B] status=422
[create_user multibyte>72B] body={"detail":[{"type":"value_error","loc":["body","password"],"msg":"Value error, password must be at most 72 bytes","input":"😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀","ctx":{"error":{}}}]}
[verify] list status=200 victim_present=False
```

**Verdict**

Defense **held**. `_within_bcrypt_byte_limit` checks `len(value.encode("utf-8"))`
(`user_schemas.py:34`), so it correctly catches a sub-72-char but >72-byte multibyte
password that a character-only bound would pass. CONFIRMS-FIXED for the multibyte axis
of AUD-02 — the documented failure mode ("multibyte password can exceed 72 bytes well
under 72 characters") is handled.

**Notes / follow-up**

Pairs with TC-IA-060 (ASCII axis) and TC-IA-062 (boundaries). Same code path; one
proof each for the ASCII and multibyte triggers.
