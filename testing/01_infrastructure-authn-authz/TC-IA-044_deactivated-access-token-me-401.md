<!--
  Test-case: TC-IA-044. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-044: Deactivated user's still-unexpired access token on `/auth/me` → 401

| Field | Value |
|---|---|
| **ID** | TC-IA-044 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
`GET /auth/me` re-checks `is_active` server-side: a user whose account is deactivated
(admin `DELETE /users/{id}`) must get `401` on `/auth/me` even with a still-unexpired
access token, because `build_authenticated_user_by_id` resolves the user and rejects
`not user.is_active` (`auth_service.py:113-115`). This is the SERVICE-backed path —
contrast TC-IA-046, where stateless role gates are NOT re-checked.

## Break hypothesis
If `/auth/me` trusted the JWT alone (no DB re-check), a deactivated user's unexpired
access token would still return `200` with their profile. The bet: `/auth/me` → `200`
after deactivation.

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>`.
- Two users: the org admin (created at onboarding) and a member `victim-<stamp>@token.test`
  created by the admin. The member is the deactivation target (deactivating the sole
  admin would trip the last-admin guard — irrelevant here).

## Steps
1. Onboard org; admin logs in. Admin creates member `victim-<stamp>@token.test`.
2. Member logs in → capture the member's access token AT (still valid, ~15 min TTL).
3. (Control) `GET /auth/me` with AT → expect `200` (active).
4. Admin `DELETE /users/{member_id}` → expect `204` (soft-deactivate).
5. `GET /auth/me` with the SAME still-unexpired AT → expect `401`.

## Expected result
- Step 3: `200` with the member's profile.
- Step 4: `204`.
- Step 5: `401` with `{"detail":"Invalid email or password."}` (InvalidCredentialsError).

## Harness
Script: `harness/tc_044.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_044.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> While active, the member's access token returned `200` on `/auth/me`. After admin
> `DELETE /users/{member}` (`204`, soft-deactivate), the SAME still-unexpired access token
> returned `401 {"detail":"Invalid email or password."}` on `/auth/me`. The service-backed
> `/auth/me` path re-checks `is_active` per request and rejects the deactivated subject,
> even though the JWT itself has not expired.

**Evidence**

```
[setup] namespace=token-19e7d332889d8a1 ... victim=victim-19e7d332889d8a1@token.example.com
[setup] onboard_org -> 201
[step1] create member -> 201  is_active=True
[step2] victim login -> 200  access token captured
[step3] /auth/me (active) -> 200  email=victim-19e7d332889d8a1@token.example.com
[step4] admin DELETE /users/{member} -> 204  body=''
[step5] /auth/me (SAME unexpired token, now deactivated) -> 401  body={"detail":"Invalid email or password."}
[verdict] deactivated-access-on-me HELD=True (before==200, delete==204, after==401)
```

**Verdict**

Defense HELD on this surface. `AuthService.build_authenticated_user_by_id`
(`auth_service.py:113-115`) resolves the subject and raises `InvalidCredentialsError`
(→401) when `not user.is_active`, so `/auth/me` enforces deactivation immediately. This is
the SERVICE-backed contrast to the documented stateless-JWT gap: TC-IA-046 shows the
role-gate (`require_company_admin`) does NOT re-check the DB and so a stale token keeps
power. Tagged CONFIRMS-DOCUMENTED because it characterizes the boundary of the tracked
"access-token denylist" deferral (`docs/FIX_BEFORE_PROD.md`): re-checks happen only where
a service resolves the row (`/auth/me`, refresh), never in the pure authZ gate.

**Notes / follow-up**

Pairs with TC-IA-045 (refresh) and TC-IA-046 (role-gate). Together they map exactly which
post-deactivation/demotion surfaces are protected (service-resolved) vs. not (stateless
JWT claim).
