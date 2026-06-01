# TC-PC-048: Email canonicalization — mixed-case stored lowercase; case-variant duplicate → 409

| Field | Value |
|---|---|
| **ID** | TC-PC-048 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Fuzz |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`admin_email` is `NormalizedEmail` (lowercased on both local-part and domain). A mixed-case email
must be stored all-lowercase (DYN-02), and a later onboard using the **lowercase variant** must be
caught as a duplicate (**409**) — case variants map to ONE identity.

## Break hypothesis
The local-part case is preserved on store (only the domain lowercased), so `Mixed.Case@x` and
`mixed.case@x` are treated as two distinct identities — defeating the global-uniqueness guarantee and
enabling duplicate accounts / case-confusion.

## Preconditions
- Live stack; demo platform admin token.
- Run-stamped: mixed `Mixed.Case.onb48.<stamp>@ONEAI.dev`; slugs `…-a` / `…-b`.

## Steps
1. Onboard slug A with the MIXED-CASE email → 201; returned admin email is lowercase.
2. Onboard NEW slug B with the LOWERCASE variant → 409.
3. **psql ground-truth:** the stored `users.email` equals its own `lower()` (all-lowercase), exactly
   one matching row.

## Expected result
Step 1 → 201, returned email lowercase. Step 2 → 409 (duplicate). Step 3 → `is_all_lowercase = t`,
one row.

## Harness
Script: `harness/tc_048.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_048.py | docker compose exec -T backend python -`
psql: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT email, (email = lower(email)) FROM users WHERE email='<lower>';"`

---

## Execution result

- **Run at:** 2026-06-01 08:57 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Mixed-case onboard 201 returned the email all-lowercase; the lowercase-variant onboard on a new
> slug returned 409; psql confirms the stored email is all-lowercase with exactly one row.

**Evidence**

```
mixed email: Mixed.Case.onb48.19e82642a679bb6@ONEAI.dev
lower email: mixed.case.onb48.19e82642a679bb6@oneai.dev
[onboard mixed-case email] status: 201
   returned admin email: mixed.case.onb48.19e82642a679bb6@oneai.dev   <-- lowercased
[onboard lowercase variant, new slug] status: 409 (expect 409)
   detail: A user with this email already exists.
```
psql ground-truth:
```
                   email                    | is_all_lowercase
--------------------------------------------+------------------
 mixed.case.onb48.19e82642a679bb6@oneai.dev | t                  <-- stored lowercase
 rows_matching_lower
---------------------
                   1                          <-- single identity
```

**Verdict**

Defense held. `NormalizedEmail` (`user_schemas.py:73`) applies `_normalize_email`
(`user_schemas.py:50-57`: `value.lower()`) to the WHOLE address — local-part included — at validation,
so the row stores lowercase and the later lowercase onboard collides on the same `email_exists`
check → 409. Confirms the DYN-02 canonicalization fix holds live (a domain-only lowercase would have
let the case-variant through as 201).

**Notes / follow-up**

Same `NormalizedEmail` guards `/auth/login` and `/platform/login`, giving case-insensitive login for
free. Pairs with TC-PC-042 (global email uniqueness).
