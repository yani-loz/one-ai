# TC-PC-014: Request-shape fuzz on `/platform/login` (extra='forbid', missing, empty)

| Field | Value |
|---|---|
| **ID** | TC-PC-014 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Fuzz |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove `/platform/login` rejects malformed request bodies at the validation layer with 422
(never 500, never an accidental auth): an unknown extra field, a missing password, and an
empty-string email must all be 422.

## Break hypothesis
If `PlatformLoginRequest` did not set `extra='forbid'`, an extra field would be silently
ignored — and worse, a request carrying valid creds **plus** junk would authenticate
(a smuggling vector). A missing/empty required field that slipped past validation could reach
the service and crash (500) or behave undefined. The bet is on a body the schema mishandles.

## Preconditions
- Live stack up; demo admin seeded. Read-only — the only request that carries valid creds
  also carries a forbidden extra field, so it must be rejected (cannot authenticate/mutate).

## Steps
1. POST a valid-cred body **plus** an extra `unexpected` field → expect 422 (extra_forbidden).
2. POST a body missing `password` → expect 422 (missing).
3. POST a body with `email: ""` → expect 422 (invalid email).
4. Bonus probes: `password: null`, missing `email`, empty `{}` body.
5. Assert all required cases are 422, nothing is 5xx, and the extra-field request did not
   return a token.

## Expected result
All three required malformed bodies → 422; no 5xx; the extra-field request is rejected (no
token issued despite valid credentials).

## Harness
Script: `harness/tc_014.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_014.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Extra field → 422 `extra_forbidden`. Missing password → 422 `missing`. Empty email → 422
> invalid email. Bonus probes (null password, missing email, empty body) all 422. No 5xx; the
> valid-creds-plus-junk request did NOT authenticate (422, no token).

**Evidence**

```
extra_field: status=422 body='{"detail":[{"type":"extra_forbidden","loc":["body","unexpected"],"msg":"Extra inputs are not permitted","input":"x"}]}'
missing_password: status=422 body='{"detail":[{"type":"missing","loc":["body","password"],"msg":"Field required","input":{"email":"super@ethera.ai"}}]}'
empty_email: status=422 body='{"detail":[{"type":"value_error","loc":["body","email"],"msg":"value is not a valid email address: An email address must have an @-sign.","input":"","ctx":{"reason":"An email address must have an @-sign."}}]}'
null_password: status=422 body='{"detail":[{"type":"string_type","loc":["body","password"],"msg":"Input should be a valid string","input":null}]}'
missing_email: status=422 body='{"detail":[{"type":"missing","loc":["body","email"],"msg":"Field required","input":{"password":"Sup3r-Dev-Only-2026!"}}]}'
empty_body: status=422 body='{"detail":[{"type":"missing","loc":["body","email"],"msg":"Field required","input":{}},{"type":"missing","loc":["body","password"],"msg":"Field required","input":{}}]}'
all_required_422=True
no_5xx_anywhere=True
extra_field_rejected_not_authed=True
VERDICT=PASS
```

**Verdict**

The defense held. `PlatformLoginRequest` sets `model_config = ConfigDict(extra="forbid")`
(`schemas/platform_schemas.py:31`) — the extra field is rejected before the service runs, so
valid credentials cannot be smuggled past the schema. `email: NormalizedEmail` (EmailStr)
rejects the empty string, and a missing/null `password` fails Pydantic's required/string-type
checks. All malformed shapes resolve to a clean 422 at the validation boundary; none reaches
bcrypt or the DB. Confirms the DYN-04 `extra='forbid'` hardening holds on the platform login.

**Notes / follow-up**

The empty-email 422 message echoes the submitted (empty) input, which is harmless here. No
follow-up; this is the expected FastAPI/Pydantic validation surface.
