# TC-PC-012: Inactive platform admin login rejected with no account-status leak

| Field | Value |
|---|---|
| **ID** | TC-PC-012 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that logging in a **deactivated** platform admin with the **correct** password returns
the same generic 401 as a wrong password — the response must not reveal that the account
exists-but-is-disabled (a partial enumeration / account-state oracle).

## Break hypothesis
If the service checked `is_active` and raised a distinct error ("account disabled") — or even
returned a structurally different 401 body — an attacker who knows a valid credential pair
for a deactivated admin could confirm the account still exists. The correct behavior collapses
unknown / wrong-password / inactive into ONE indistinguishable failure.

## Preconditions
- Live stack up. Throwaway pool admin `tw-inactive-tw06012c3@oneai.dev` (password
  `Valid-Pass-2026!`) exists with `is_active=false` (ground-truth confirmed via psql).
- Read-only: only the correct password (which cannot succeed for an inactive account) and
  wrong passwords are sent; no account is mutated. This account is shared with the PSES suite
  but never written by this case.

## Steps
1. POST `/platform/login` as `tw-inactive` with the **correct** password.
2. POST `/platform/login` as `tw-inactive` with a **wrong** password (generic-401 baseline).
3. POST `/platform/login` as the active demo admin with a **wrong** password (cross-check).
4. Assert all three → 401, the inactive-correct body is byte-identical to both wrong-password
   bodies, and the body contains no "inactive/disabled/deactivated/suspended" wording.

## Expected result
All three → 401 with identical generic bodies; no account-status keyword leaks.

## Harness
Script: `harness/tc_012.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_012.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The inactive admin + correct password → 401 with `{"detail":"Invalid email or password."}`
> — byte-identical to the wrong-password 401 for both the same inactive admin and the active
> demo admin. No status keyword leaked.

**Evidence**

```
inactive_correct_pw_status=401
inactive_correct_pw_body='{"detail":"Invalid email or password."}'
inactive_wrong_pw_status=401
inactive_wrong_pw_body='{"detail":"Invalid email or password."}'
active_wrong_pw_status=401
active_wrong_pw_body='{"detail":"Invalid email or password."}'
body_matches_inactive_wrong_pw=True
body_matches_active_wrong_pw=True
body_leaks_account_status=False
VERDICT=PASS
```

Ground-truth (psql) confirming the account is genuinely inactive:

```
                  id                  |              email              |     full_name      | is_active
 aa3ff002-58d6-4cd4-98cf-6c1884dac867 | tw-inactive-tw06012c3@oneai.dev | Throwaway Inactive | f
```

**Verdict**

The defense held. `PlatformAuthService.login` folds the inactive case into the single
`if admin is None or not admin.is_active or not password_ok: raise InvalidCredentialsError(...)`
guard (`platform_auth_service.py:87-88`), and bcrypt runs unconditionally first (the real
hash is used because the admin row exists), so the inactive-correct path is timing- and
body-indistinguishable from a wrong password. No account-state oracle.

**Notes / follow-up**

Note the inactive account still pays a real bcrypt verify (its row exists, so its real hash —
not `DUMMY_PASSWORD_HASH` — is used); this keeps it indistinguishable from an active
wrong-password attempt, which is the desired property.
