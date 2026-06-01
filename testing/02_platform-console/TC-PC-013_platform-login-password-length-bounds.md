# TC-PC-013: Password length boundaries on `/platform/login` (no 500; bound divergence)

| Field | Value |
|---|---|
| **ID** | TC-PC-013 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Boundary |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | NEW |

## Objective
Verify that out-of-range password lengths on `/platform/login` never produce a 500. The
brief's hypothesis was that a 200-char password (>72 bytes) and a 7-char password (<8) would
both be rejected with 422 by a `BcryptPassword`-typed field. This case tests that hypothesis
empirically and characterizes the **real** validation contract of `PlatformLoginRequest`.

## Break hypothesis
A password longer than bcrypt's hard 72-byte limit, if it reaches `bcrypt.hashpw`/`checkpw`
unguarded, makes bcrypt 5.x raise `ValueError` → an opaque **500**. The `BcryptPassword`
type (used by user-create) prevents that at the validation boundary. The platform LOGIN
field, however, is `password: str = Field(min_length=1, max_length=256)` — NOT
`BcryptPassword` — so the 422-on-short/422-on-overlong assumption is expected to be **false**;
the security-relevant question is whether the overlong password instead surfaces as a 500.

## Preconditions
- Live stack up; demo admin seeded. Read-only (only wrong-by-construction passwords sent).
- Three probes: 7 chars, 200 chars (= 200 bytes, > 72), 257 chars (> the 256 max_length).

## Steps
1. POST `/platform/login` with a 7-char password.
2. POST with a 200-char password (>72 UTF-8 bytes).
3. POST with a 257-char password (> `max_length=256`).
4. Assert no response is 5xx; record each status against the brief's 422 expectation.

## Expected result
**Security invariant (must hold):** no 5xx on any probe; the only true bound (`max_length=256`)
yields 422 for the 257-char probe.
**Brief's assumption (under test):** 7-char → 422 and 200-char → 422. Expected to be refuted
because the field is not `BcryptPassword`; the 7-char and 200-char probes instead reach the
auth path and return a generic **401**.

## Harness
Script: `harness/tc_013.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_013.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** NEW

**Actual behavior**

> 7-char → **401** (not 422). 200-char (>72 bytes) → **401** (not 422, and crucially not 500).
> 257-char → **422** (`string_too_long`, max_length 256). No 5xx anywhere. The brief's
> short/overlong-are-422 assumption is refuted; the security invariant (no 500) holds.

**Evidence**

```
7char: status=401 body='{"detail":"Invalid email or password."}'
200char_72byte: status=401 body='{"detail":"Invalid email or password."}'
257char_over_max: status=422 body='{"detail":[{"type":"string_too_long","loc":["body","password"],"msg":"String should have at most 256 characters",...}]}'
no_server_error_5xx=True
seven_char_is_422(brief_expected)=False  actual_status=401
overlong_200_is_422(brief_expected)=False  is_500=False  actual_status=401
over_max_257_is_422=True  actual_status=422
security_invariant_held(no_500 + maxlen_422)=True
brief_assumption_held(short&overlong_are_422)=False
VERDICT=PASS; CONCERN=YES (no min_length=8 / no <=72-byte bound on platform login password)
```

**Verdict**

The **security defense held** (no 500): the >72-byte password reaches `verify_password`, which
catches bcrypt's `ValueError` and returns `False` (`security/password.py:51-54`), yielding a
clean generic 401 instead of a 500 — the same belt that protects company login. So this is
**not** a vulnerability.

But there is a **NEW, real divergence** worth recording: `PlatformLoginRequest.password` is
`Field(min_length=1, max_length=256)` (`schemas/platform_schemas.py:34`), **not** the
`BcryptPassword` type (`min_length=8` + `<=72`-byte `AfterValidator`) used by
`UserCreateRequest` (`schemas/user_schemas.py:43,89`). The same is true of company login
(`auth_schemas.py:28`). Consequences:
- No `min_length=8` floor on login (a 1-char password is accepted into the auth path).
- The 72-byte bcrypt bound is not asserted at the boundary; it is caught downstream by
  `verify_password`'s `try/except` instead — a defense-in-depth gap, not a live defect.

Severity Low: the only outward effect today is that short/overlong passwords answer 401 rather
than 422 (slightly noisier validation; no 500, no leak). The concern is the **asymmetry** — if
a future refactor of `verify_password` removed the `except ValueError`, the unbounded login
field would immediately become a 500 oracle. Login is the right place to also bound input.

**Notes / follow-up**

Recommend typing the login password fields with a bounded variant (a non-min_length-8 sibling
of `BcryptPassword` that still enforces the `<=72`-byte cap), so the byte cap lives at the
schema boundary on every bcrypt-consuming surface, not only on user-create.

This is **distinct** from the tracked `FIX_BEFORE_PROD.md` item "Add a password policy + breach
check" (which targets *set-time* complexity/length policy on password **creation**). The finding
here is the **byte-cap asymmetry on the LOGIN field**: `verify_password`'s `<=72`-byte limit is
asserted at the schema boundary on the create path (`BcryptPassword`) but **not** on either
login path (`PlatformLoginRequest`/`LoginRequest` use plain `Field(min_length=1,
max_length=256)`), leaving login to rely solely on `verify_password`'s downstream `except
ValueError`. That specific defense-in-depth gap is not captured by the set-time-policy item →
tagged NEW.
