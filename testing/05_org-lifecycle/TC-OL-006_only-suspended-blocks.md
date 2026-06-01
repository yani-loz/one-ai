# TC-OL-006: Only `suspended` blocks — `offboarded`/`onboarding` still log in

| Field | Value |
|---|---|
| **ID** | TC-OL-006 · **Target** Org Lifecycle (PC-03a) · **Suite** SUSPEND |
| **Type** | Adversarial · **Severity if fail** Low · **Status** Executed |
| **Result** | ⚠️ Pass-with-concern · **Finding tag** CONFIRMS-DOCUMENTED (offboarded cutoff → PC-06) |

## Objective
Confirm the suspend gate keys on exactly `suspended` — `onboarding` and `offboarded` orgs are allowed to
authenticate (`auth_service._load_loginable_org`).

## Break hypothesis
`offboarded` *looks* terminal, so an operator might assume an offboarded org's users are locked out. They
are not — login still succeeds. The access cutoff for `offboarded` is deferred to PC-06 (erasure).

## Steps / Harness
`provision_company("sus006")` → set status `offboarded` → login; set `onboarding` → login.
`harness/_finish_suspend.py` (case 006).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ⚠️ Pass-with-concern · **Tag:** CONFIRMS-DOCUMENTED

**Evidence**
```
[006] status=offboarded -> login: 200 (only 'suspended' blocks — offboarded cutoff is PC-06)
[006] status=onboarding -> login: 200
```

**Verdict**
Correct per the explicit design (`auth_service.py:160` checks `== suspended` only; the method is named
`_load_loginable_org`, not `_load_active_org`, exactly to signal this). The only caveat is semantic: a status
that reads as terminal (`offboarded`) still permits login until PC-06 lands the erasure/access-cutoff. Tracked
in the epic's "out of scope → PC-06". CONFIRMS-DOCUMENTED.

**Notes** If a hard cutoff for `offboarded` is wanted before PC-06, add it to `_load_loginable_org`.
