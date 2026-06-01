# TC-PC-015: Case-insensitive platform login via email canonicalization (DYN-02)

| Field | Value |
|---|---|
| **ID** | TC-PC-015 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Fuzz |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that the demo platform admin logs in with an UPPERCASE (and mixed-case) email + the
correct password → 200, because `NormalizedEmail` lowercases the whole address (local-part
included). Confirms case-insensitive login (DYN-02) and that all case variants resolve to the
SAME admin identity.

## Break hypothesis
If the local-part were not lowercased (e.g. relying only on EmailStr, which lowercases just
the domain), `SUPER@ETHERA.AI` would not match the stored `super@ethera.ai` and would 401 —
or, if a second row somehow matched, it would resolve a different `sub`. Either is a
canonicalization defect (locked-out legitimate user, or split identity).

## Preconditions
- Live stack up; demo admin `super@ethera.ai` (id `609f2b17-bee9-4f7f-a26d-cb08f666497a`)
  seeded. Read-only: only the correct password is sent (cannot mutate the account).

## Steps
1. POST `/platform/login` with the canonical lowercase email + correct password.
2. POST with `SUPER@ETHERA.AI` (all upper) + correct password.
3. POST with `Super@Ethera.AI` (mixed) + correct password.
4. Assert all three → 200 and decode each access token; the `sub` must be identical across all
   three (one identity), and the uppercase login body must still exclude `user`.

## Expected result
All three → 200, identical `sub` across cases, `user` field absent.

## Harness
Script: `harness/tc_015.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_015.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Lowercase, uppercase, and mixed-case emails all → 200, and all three access tokens carry the
> identical `sub=609f2b17-bee9-4f7f-a26d-cb08f666497a`. The uppercase login body excludes
> `user`.

**Evidence**

```
lower_email='super@ethera.ai' status=200
upper_email='SUPER@ETHERA.AI' status=200
mixed_email='Super@Ethera.AI' status=200
sub_lower=609f2b17-bee9-4f7f-a26d-cb08f666497a
sub_upper=609f2b17-bee9-4f7f-a26d-cb08f666497a
sub_mixed=609f2b17-bee9-4f7f-a26d-cb08f666497a
upper_has_user_field=False
all_three_200=True same_admin_id_across_case=True
VERDICT=PASS
```

psql cross-check (the `sub` matches the seeded admin row):

```
                  id                  |     email       |     full_name      | is_active
 609f2b17-bee9-4f7f-a26d-cb08f666497a | super@ethera.ai | Ethera Super Admin | t
```

**Verdict**

The defense held. `NormalizedEmail`'s `_normalize_email` lowercases the entire address before
lookup (`schemas/user_schemas.py:50-57,73`), so every case variant canonicalizes to the single
stored identity and matches on login — and resolves to the same `sub`, proving it is one
account, not a coincidental second match. Confirms the DYN-02 case-insensitive-login fix holds
on the platform login surface.

**Notes / follow-up**

`NormalizedEmail` is shared by the company-auth and onboarding schemas, so this canonicalization
property is consistent across all three login surfaces. No follow-up.
