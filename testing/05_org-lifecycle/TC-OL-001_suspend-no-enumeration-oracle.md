<!--
  SUSPEND suite — suspend-blocks-login gate (the ⭐ security core). See ../README.md (when
  it lands) and ../../README.md for legend/tags.
-->

# TC-OL-001: Suspend gate is **not** a user-enumeration oracle (DISCRIMINATING)

| Field | Value |
|---|---|
| **ID** | TC-OL-001 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | SUSPEND — suspend-blocks-login gate ⭐ |
| **Type** | Adversarial (DISCRIMINATING) |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-03a-AC3's no-oracle property: a suspended org's company login returns **403 only when
the credentials are valid**; a wrong password or an unknown email returns the **generic 401**
whose body is **byte-identical** to a non-suspended wrong-password 401. The 403 must not leak the
existence of a suspended org to an attacker who lacks valid credentials.

## Break hypothesis
If the suspension check ran *before* the bcrypt credential check (or short-circuited it), then a
suspended org would answer 403 even to a wrong password / unknown email — turning the 403 into an
enumeration oracle ("this account exists and its org is suspended"). The defense is the **ordering**:
`verify_password` + `is_active` are checked first (`auth_service.py:73`); only on success does
`_load_loginable_org` (`auth_service.py:79`) raise `OrganizationSuspendedError` (403).

## Preconditions
- Live stack up. One fresh run-stamped org provisioned via `provision_company(c, plat, "sus001")`
  (slug `sus001-<stamp>`, admin `admin-sus001-<stamp>@oneai.dev`). Throwaway — safe to suspend.
- A **baseline** wrong-password 401 captured while the org is STILL ACTIVE (the reference body).

## Steps
1. Provision a fresh company; capture a wrong-password 401 against it **while active** (baseline).
2. Suspend the org (`PATCH .../status` → `suspended`).
3. (a) Login with VALID creds → expect 403. (b) Login with WRONG password → expect 401.
   (c) Login with an UNKNOWN email → expect 401.
4. Assert bodies (b) and (c) are **byte-identical** to the active baseline; (a)'s 403 body differs.
5. Reactivate the org (cleanup).

## Expected result
- Baseline + (b) + (c): `401 {"detail":"Invalid email or password."}` — byte-identical.
- (a): `403 {"detail":"Your organization's access is suspended."}` — a *different* body, reachable
  only with valid credentials.

## Harness
Script: `harness/tc_001.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_001.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The active-org wrong-password baseline returned `401 {"detail":"Invalid email or password."}`.
> After suspension: valid creds → `403 {"detail":"Your organization's access is suspended."}`;
> wrong password → the SAME `401` body as the baseline; unknown email → the SAME `401` body. The
> 403 is reachable only with valid credentials, and the two failed-credential bodies are byte-for-byte
> identical to a non-suspended failure — so suspension leaks nothing to an attacker without valid creds.

**Evidence**

```
== TC-OL-001 — suspend gate is NOT an enumeration oracle (DISCRIMINATING, AC3) ==
[setup]   org 46589872-8361-44bd-8547-38bbf72cae7a admin admin-sus001-19e835577083b96@oneai.dev
[baseline] active-org wrong-pw: 401 body=b'{"detail":"Invalid email or password."}'
[suspend]  PATCH status=suspended: 200 status=suspended
[a valid]  suspended valid creds: 403 body=b'{"detail":"Your organization\'s access is suspended."}'
[b wrong]  suspended wrong-pw:    401 body=b'{"detail":"Invalid email or password."}'
[c unknown] unknown email:        401 body=b'{"detail":"Invalid email or password."}'
RESULT: PASS — 403 only with valid creds; (b)/(c) byte-identical to active baseline -> NO oracle
```

**Verdict**

The defense held. `AuthService.login` runs the bcrypt credential check first —
`password_ok = verify_password(...)` then `if user is None or not user.is_active or not password_ok:
raise InvalidCredentialsError` (`backend/app/identity/services/auth_service.py:72-74`) — and only
**after** that passes does it call `_load_loginable_org` (`auth_service.py:79`), which raises
`OrganizationSuspendedError` → 403 (`error_handlers.py:43`). Because the dummy-hash path makes an
unknown email cost the same as a real one, the wrong-pw and unknown-email 401 bodies are byte-identical
to the active baseline. The 403 is a *post-authentication* signal, not an enumeration oracle.
CONFIRMS-FIXED (PC-03a-AC3 no-oracle property), verified live.

**Notes / follow-up**
The discriminator is the byte-comparison, not the status codes alone: identical 401 bodies prove the
suspended org is indistinguishable from any failed login. Pairs with TC-OL-007 (the same gate driven
end-to-end through the PATCH endpoint).
