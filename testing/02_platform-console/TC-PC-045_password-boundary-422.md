# TC-PC-045: Password boundary — short, overlong, and multibyte-byte-limit → 422

| Field | Value |
|---|---|
| **ID** | TC-PC-045 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`admin_password` is `BcryptPassword` — `min_length=8`, `max_length=128` CHARS, **and** `<=72 UTF-8
bytes`. A 7-char, a 200-char, and a multibyte password under 128 chars but over 72 BYTES must each
return **422** (the byte limit must NOT surface as a 500 from bcrypt raising).

## Break hypothesis
The multibyte password (30 emoji = 30 chars, 120 bytes) passes the char-length check, reaches
`hash_password`, and bcrypt 5.x **raises** on the >72-byte input — surfacing as an opaque **500**
instead of a clean 422.

## Preconditions
- Live stack; demo platform admin token.
- Cases: `"Abc123!"` (7 chars), `"A"*200` (200 chars), `"\U0001F600"*30` (30 emoji = 120 bytes).

## Steps
1. For each password, `onboard_org` with otherwise-valid payload + a unique slug.
2. Assert 422 and the Pydantic error type/message.

## Expected result
7-char → 422 `string_too_short`; 200-char → 422 `string_too_long`; 30-emoji → 422 `value_error`
("password must be at most 72 bytes"). **No 500.**

## Harness
Script: `harness/tc_045.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_045.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All three rejected with 422. Critically, the 30-emoji / 120-byte password returned a clean
> `value_error` ("password must be at most 72 bytes"), NOT a 500 from bcrypt.

**Evidence**

```
[7-char (under min 8)] chars=7 bytes=7 -> status=422
   422 detail loc/type/msg: [(['body', 'admin_password'], 'string_too_short', 'String should have at least 8 characters')]
[200-char (over max 128)] chars=200 bytes=200 -> status=422
   422 detail loc/type/msg: [(['body', 'admin_password'], 'string_too_long', 'String should have at most 128 characters')]
[30-emoji (120 bytes > 72, chars < 128)] chars=30 bytes=120 -> status=422
   422 detail loc/type/msg: [(['body', 'admin_password'], 'value_error', 'Value error, password must be at most 72 bytes')]
```

**Verdict**

Defense held. `BcryptPassword` (`user_schemas.py:43-45`) layers `Field(min_length=8, max_length=128)`
with `AfterValidator(_within_bcrypt_byte_limit)` (`user_schemas.py:28-39`), which checks
`len(value.encode("utf-8")) > 72` and raises a ValueError → 422 BEFORE the password reaches
`hash_password`. The multibyte 120-byte case is the load-bearing one (a naive char-only bound would
500 here); it returns 422. Tagged CONFIRMS-FIXED — this is the live re-proof of **AUD-02/AUD-09**
(`docs/audits/2026-05-30_identity-module-deep-audit.md`): the pinned bcrypt 5.x *raises* on >72-byte
input, which was an opaque 500 on `POST /platform/orgs` before the `_within_bcrypt_byte_limit` guard
landed. The guard holds live → clean 422, no 500.

**Notes / follow-up**

Mirrors DYN handling in `user_schemas`; the same `BcryptPassword` type guards `POST /users` (Target
01 surface) — the byte-limit defense is shared.
