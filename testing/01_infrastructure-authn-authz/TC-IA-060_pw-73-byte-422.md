# TC-IA-060: 73-byte ASCII password → 422 (not 500)

| Field | Value |
|---|---|
| **ID** | TC-IA-060 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Boundary |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Verify the AUD-02 fix: a 73-byte ASCII password (one byte over bcrypt's 72-byte hard
limit) submitted to `POST /users` is rejected at the validation boundary with a clean
`422` from the `BcryptPassword` `AfterValidator` — NOT an opaque `500`, and NO user is
created.

## Break hypothesis
If the byte-limit guard (`_within_bcrypt_byte_limit`) is missing or bypassed, the
over-long password reaches `hash_password` → `bcrypt.hashpw`, which under bcrypt 5.x
raises `ValueError`. That `ValueError` is not an `IdentityError`, so it bubbles to
FastAPI's default handler → **HTTP 500**, with no account created — exactly the AUD-02
defect. A 500 here would REFUTE the fix.

## Preconditions
- Live stack (`http://localhost:8000`), DB persistent/shared.
- Namespace stamp `iv-060-*`; fresh run-stamped org + admin onboarded via platform admin.
- The demo org/admin are never touched.

## Steps
1. Platform-login, onboard a fresh org `iv-060-<stamp>` with a fresh company_admin.
2. Company-login as that admin to get an access token.
3. `POST /users` with `password = "A" * 73` (73 ASCII bytes, 73 chars).
4. `GET /users` and assert the would-be victim email is absent.

## Expected result
`422 Unprocessable Entity`, body a Pydantic validation error on `body.password`
("password must be at most 72 bytes"). No `500`. `GET /users` does not list the victim.

## Harness
Script: `harness/tc_060.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_060.py`

---

## Execution result

- **Run at:** 2026-05-31 12:00 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED (AUD-02)

**Actual behavior**

> The 73-byte password was rejected with a clean `422` from the `BcryptPassword`
> `AfterValidator`. No `500` occurred and no user was created — the AUD-02 fix holds.

**Evidence**

```
[onboard] status=201
[probe] password length: chars=73 bytes=73
[create_user pw=73B] status=422
[create_user pw=73B] body={"detail":[{"type":"value_error","loc":["body","password"],"msg":"Value error, password must be at most 72 bytes","input":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","ctx":{"error":{}}}]}
[verify] list status=200 emails=['iv-060-admin-19e7d2e2c6d173e@example.com']
[verify] victim_present=False
```

**Verdict**

Defense **held**. The validator at `backend/app/identity/schemas/user_schemas.py:25-42`
(`_within_bcrypt_byte_limit` wired into `BcryptPassword`) intercepts the over-long
password before `hash_password` (`security/password.py:26`) is ever reached, converting
the would-be `500` into a `422`. This empirically CONFIRMS the AUD-02 remediation
("guard password length: 422 on >72 UTF-8 bytes"). No 500, no orphan account.

**Notes / follow-up**

Multibyte axis of the same fix is covered by TC-IA-061; the upper-bound (exactly 72
bytes → 201) by TC-IA-062. Related prior finding: `docs/audits/2026-05-30_identity-module-deep-audit.md` §AUD-02 / AUD-09.
