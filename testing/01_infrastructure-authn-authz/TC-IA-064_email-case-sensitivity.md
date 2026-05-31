# TC-IA-064: Email case-sensitivity → duplicate identities + case-fragile login

| Field | Value |
|---|---|
| **ID** | TC-IA-064 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ❌ Fail → ✅ **FIXED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
Determine whether email addresses are normalized before storage / uniqueness checks /
login lookup. `EmailStr` (email-validator) lowercases ONLY the domain, preserving
local-part case; `email_exists` / `get_by_email` are EXACT-match. The probe: can two
DISTINCT user identities exist for the same human address differing only in local-part
case, and is login case-fragile?

## Break hypothesis
Because the local-part case is preserved and all lookups are exact-match:
1. `Foo.Bar@Example.com` and `foo.bar@example.com` both create successfully (two rows,
   two identities, one human) — a uniqueness / account-takeover-adjacent hazard.
2. A user stored with mixed-case local-part cannot log in with the lowercased form
   (`get_by_email` misses) → `401` — login is case-fragile and surprising.

If instead the system normalizes (2nd create → `409`, opposite-case login → `200`),
this is NA.

## Preconditions
- Live stack; namespace `iv-064-*`; fresh org + admin. Unique local-parts per run so no
  collision with parallel suites.

## Steps
1. Onboard fresh org + admin; company-login.
2. `POST /users` with `Foo.Bar.<stamp>@Example.com`, then `foo.bar.<stamp>@example.com`.
3. `GET /users` to show the stored emails verbatim.
4. Create a THIRD user `Solo.User.<stamp>@Example.com` with NO lowercase twin, then
   `POST /auth/login` with `solo.user.<stamp>@example.com` (opposite local-part case)
   → expect `401`; and with the exact stored form → expect `200` (control).

## Expected result (contract the system *should* honor)
A single canonical identity per address: the second create should `409` (duplicate) and
opposite-case login should succeed (`200`). Deviation = finding.

## Harness
Script: `harness/tc_064.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_064.py`

---

## Execution result

- **Run at:** 2026-05-31 12:05 local
- **Result:** ❌ Fail (a win — NEW defect reproduced)
- **Finding tag:** NEW

**Actual behavior**

> Both axes broke. (1) `Foo.Bar.<s>@Example.com` and `foo.bar.<s>@example.com` BOTH
> created successfully (`201`/`201`) as two distinct rows — two identities for one human
> address; the domain was lowercased but the local-part case was preserved verbatim.
> (2) The solo mixed-case user (no twin) could NOT log in with its lowercased local-part
> → `401 "Invalid email or password."`, while the exact-case form logged in → `200`.
> Login is exact-match and therefore case-fragile.

**Evidence**

```
[onboard] status=201
[inputs] mixed='Foo.Bar.19e7d3101e885db@Example.com' lower='foo.bar.19e7d3101e885db@example.com'
[create #1 mixed] status=201 stored_email=Foo.Bar.19e7d3101e885db@example.com
[create #2 lower] status=201 stored_email=foo.bar.19e7d3101e885db@example.com
[DISTINCT IDENTITIES FOR SAME HUMAN] both_201=True
[GET /users] stored_emails=['Foo.Bar.19e7d3101e885db@example.com', 'foo.bar.19e7d3101e885db@example.com', 'iv-064-admin-19e7d3101e885db@example.com']
[create #3 solo-mixed] status=201 stored_email='Solo.User.19e7d3101e885db@example.com'
[login solo-user w/ OPPOSITE-case local-part] stored='Solo.User.19e7d3101e885db@example.com' attempt='solo.user.19e7d3101e885db@example.com' status=401 body={"detail":"Invalid email or password."}
[login solo-user w/ EXACT case] attempt='Solo.User.19e7d3101e885db@example.com' status=200 (control — must be 200)
```

**Verdict**

Defense **broke** — **NEW**, severity **Medium**.

- **Root cause / blast radius.** Email is never canonicalized. `EmailStr` normalizes the
  domain to lowercase but preserves local-part case; the uniqueness check
  `UserRepository.email_exists` (`user_repository.py:76-79`) and the login lookup
  `UserRepository.get_by_email` (`user_repository.py:38-45`) both use exact `==` match.
  The `users.email` UNIQUE index is therefore exact-match too. Consequences:
  1. **Duplicate identities for one human** — two accounts can be onboarded for the same
     address (`Foo.Bar@…` vs `foo.bar@…`), defeating the "one user = one email"
     assumption that login and `get_by_email`'s own docstring rely on. The global
     `email_exists` precheck is bypassed by a case variant, so the duplicate-prevention
     intent is partial.
  2. **Case-fragile login** — a user provisioned with a mixed-case local-part is locked
     out if they type the lowercased form (the common, expected normalization on most
     platforms), getting an indistinguishable generic `401`. This is a real UX/lockout
     and support-load defect, and a latent vector for confusion/duplicate-account abuse.
- **Not in the baseline.** No AUD-* finding and no `docs/FIX_BEFORE_PROD.md` item covers
  email canonicalization — confirmed by grep over both. Tagged **NEW**.

**Notes / follow-up**

Remediation: normalize the email to a single canonical form (lowercase the whole address,
or apply RFC-aware folding) at the schema boundary (a `BeforeValidator`/`field_validator`
on `email` in `user_schemas.py` and `platform_schemas.py`) so storage, the UNIQUE index,
`email_exists`, and `get_by_email` all agree. Note the existing UNIQUE index will not
retroactively dedupe the case variants already creatable today.

---

## Remediation (2026-05-31) — ✅ FIXED

Email is now **canonicalized to lowercase** at the schema boundary (`NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]`), applied to every email input (`UserCreateRequest`, `LoginRequest`, `OrganizationCreateRequest`, `PlatformLoginRequest`). Storage, the UNIQUE index, `email_exists`, and `get_by_email` now agree on one canonical form — case variants are a single identity and login is case-insensitive.

- **Code:** `NormalizedEmail` in `backend/app/identity/schemas/user_schemas.py`; used across `user_schemas` / `auth_schemas` / `platform_schemas`.
- **Re-verified live (this harness):** `[create #1 mixed] 201` (stored lowercase), `[create #2 lower] 409` (duplicate now caught), opposite-case login `200`, `both_201=False`.
- **Regression tests:** `test_user_routes.py::test_create_user_canonicalizes_email_to_lowercase`, `::test_create_user_duplicate_email_case_insensitive_returns_409`, `test_auth_routes.py::test_login_email_is_case_insensitive`.
- **Tracked:** DYN-02. (Pre-fix case-variant rows aren't retroactively deduped — moot after the test-DB truncate + reseed.)
