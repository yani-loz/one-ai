<!--
  Test-case: TC-IA-041. See ../README.md for legend, tags, severity scale.
-->

# TC-IA-041: Refresh response returns a token pair and EXCLUDES the `user` field

| Field | Value |
|---|---|
| **ID** | TC-IA-041 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Token lifecycle |
| **Type** | Positive |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
`POST /auth/refresh` returns exactly `{access_token, refresh_token, token_type}` and
omits the `user` field. The route declares `response_model_exclude_none=True`
(`auth_routes.py:39`) and `AuthService.refresh` builds `TokenPairResponse` without a
`user` (`auth_service.py:95`), so `user` is `None` and must be stripped.

## Break hypothesis
If `response_model_exclude_none` were dropped or `refresh` populated `user`, the refresh
body would carry a `user` object — leaking the user view on rotation, contradicting SPEC
§4 (user is returned only on `/auth/login`). The bet: `user` is present in the body.

## Preconditions
- Live stack; fresh run-stamped org `token-<stamp>` + admin logged in.

## Steps
1. Onboard a fresh org; `POST /auth/login` as admin → capture refresh R0 (login body DOES
   include `user`, as a control).
2. `POST /auth/refresh` with R0 → inspect the response body's exact key set.

## Expected result
- Login body keys ⊇ `{access_token, refresh_token, token_type, user}` (control).
- Refresh body keys == `{access_token, refresh_token, token_type}` — NO `user` key.
- `token_type == "bearer"`.

## Harness
Script: `harness/tc_041.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_041.py`

---

## Execution result

- **Run at:** 2026-05-31 (local)
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Login body carried all four keys including `user` (control). The refresh body carried
> exactly `{access_token, refresh_token, token_type}` with `token_type == "bearer"` and
> NO `user` key — `response_model_exclude_none=True` stripped the null `user`. The full
> refresh body is shown verbatim below; it contains no `user` field and no
> hash/credential.

**Evidence**

```
[setup] namespace=token-19e7d32e1d73a6d slug=token-19e7d32e1d73a6d admin=admin-19e7d32e1d73a6d@token.example.com
[setup] onboard_org -> 201
[step1] login -> 200  body_keys=['access_token', 'refresh_token', 'token_type', 'user']  has_user=True
[step2] refresh -> 200
[step2] refresh body keys = ['access_token', 'refresh_token', 'token_type']
[step2] full refresh body = {'access_token': 'eyJhbGciOiJIUzI1NiIs...Uv3di3s', 'refresh_token': 'iTah_sM740tM...KrKG', 'token_type': 'bearer'}
[step2] token_type = 'bearer'
[step2] 'user' in body = False
[verdict] excludes-user HELD=True (refresh keys exact 3, no user; login had user)
```

**Verdict**

Defense HELD (positive contract). `auth_routes.py:39` (`response_model_exclude_none=True`)
+ `AuthService.refresh` (`auth_service.py:95`, builds `TokenPairResponse` with no `user`)
produce a refresh body of exactly the three token fields. SPEC §4 honored — the user view
is returned only on `/auth/login`.

**Notes / follow-up**

None. The login control proves the exclusion is specific to the refresh response, not a
blanket schema change.
