# TC-PC-041: Duplicate slug → 409

| Field | Value |
|---|---|
| **ID** | TC-PC-041 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | ONB — Onboarding contracts + input validation/fuzz |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Onboarding the same `org_slug` twice (with a different admin email the second time) is rejected
with **409** — slug global-uniqueness is enforced.

## Break hypothesis
The second onboard succeeds (201) or surfaces a 500 from the `organizations.slug` UNIQUE violation
instead of a clean 409 — meaning the pre-insert slug check and/or the IntegrityError→409 mapping is
broken.

## Preconditions
- Live stack; demo platform admin token.
- Run-stamped slug `onb41-<stamp>`; two distinct emails `…-a@oneai.dev`, `…-b@oneai.dev`.

## Steps
1. Onboard slug `S` with email A → expect 201.
2. Onboard the SAME slug `S` with email B → expect 409.

## Expected result
First: 201. Second: 409 with `detail` "An organization with this slug already exists."

## Harness
Script: `harness/tc_041.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_041.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 08:52 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> First onboard 201; the duplicate-slug onboard returns 409 with the generic slug-conflict message.

**Evidence**

```
first onboard (slug S): 201 -> {'organization': {'id': '3ecb3365-...', 'slug': 'onb41-19e826234c50f5e', 'status': 'active', 'user_count': 1, ...}, 'admin': {...}}
second onboard (same slug, diff email): 409 -> {'detail': 'An organization with this slug already exists.'}
```

**Verdict**

Defense held. The pre-insert guard `if await self._organizations.get_by_slug(...) is not None`
(`platform_auth_service.py:154-155`) raises `DuplicateOrganizationError`, mapped to 409 by
`error_handlers.py:45`. (The IntegrityError fallback at `platform_auth_service.py:163-167` covers
the concurrent-race path — exercised by the RACE suite, not here.)

**Notes / follow-up**

The race-condition variant (two same-slug onboards firing concurrently) is the RACE suite's job.
