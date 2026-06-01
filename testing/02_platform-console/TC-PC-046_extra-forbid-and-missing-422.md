# TC-PC-046: Fuzz — extra unknown field forbidden, missing required field → 422

| Field | Value |
|---|---|
| **ID** | TC-PC-046 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Fuzz |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`OrganizationCreateRequest` has `model_config = ConfigDict(extra="forbid")`. An onboarding body with
an **unknown field** must be rejected with **422** (no silent mass-assignment), and a body **missing
a required field** (`admin_password`) must also be 422.

## Break hypothesis
An unknown field (e.g. `is_superuser`) is silently accepted/ignored — a mass-assignment /
privilege-escalation foothold if a future field is mapped. Or a missing required field reaches the
service and 500s instead of 422.

## Preconditions
- Live stack; demo platform admin token.
- Run-stamped slugs `onb46-<stamp>-extra` / `…-missing`.

## Steps
1. POST a fully-valid body PLUS an extra `"is_superuser": true` field → expect 422 `extra_forbidden`.
2. POST a body OMITTING `admin_password` → expect 422 `missing`.

## Expected result
Both → 422; first with `type=extra_forbidden` on `is_superuser`, second with `type=missing` on
`admin_password`. No write, no 500.

## Harness
Script: `harness/tc_046.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_046.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:55 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Extra `is_superuser` rejected with `extra_forbidden` (422); missing `admin_password` rejected with
> `missing` (422). No field silently absorbed.

**Evidence**

```
[extra unknown field 'is_superuser'] status: 422
   detail: [{'type': 'extra_forbidden', 'loc': ['body', 'is_superuser'], 'msg': 'Extra inputs are not permitted', 'input': True}]
[missing admin_password] status: 422
   detail: [{'type': 'missing', 'loc': ['body', 'admin_password'], 'msg': 'Field required', 'input': {'org_name': 'Org Missing', 'org_slug': 'onb46-19e82634ab0da8c-missing', 'admin_email': 'onb46-19e82634ab0da8c-missing@oneai.dev', 'admin_full_name': 'Missing Admin'}}]
```

**Verdict**

Defense held. `OrganizationCreateRequest.model_config = ConfigDict(extra="forbid")`
(`platform_schemas.py:40`) rejects the unknown `is_superuser` (DYN-04 mass-assignment defense), and
the required `admin_password` field (`platform_schemas.py:46`) yields a `missing` error when omitted.
Tagged CONFIRMS-FIXED — live re-proof of the **DYN-04** extra-forbid (mass-assignment) hardening
cited in `user_schemas.py` and the identity audit family, now confirmed on the platform onboarding body.

**Notes / follow-up**

The same `extra="forbid"` guard protects `UserCreateRequest`/`UserUpdateRequest`/`PlatformLoginRequest`.
