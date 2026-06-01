# TC-PC-010: Platform login happy path returns a token pair with NO `user` field

| Field | Value |
|---|---|
| **ID** | TC-PC-010 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove the demo platform admin can log in and that `/platform/login` returns **exactly**
`{access_token, refresh_token, token_type}` — the platform domain has no user view, so the
nullable `user` field of `TokenPairResponse` must be excluded (`response_model_exclude_none=True`).

## Break hypothesis
The route shares `TokenPairResponse` with `/auth/login` (which carries a populated `user`).
If `response_model_exclude_none=True` were dropped (or `user` were populated), the platform
login body would leak a `user` key (a null or, worse, a synthesised identity) — a contract
violation and a foothold for the frontend to mis-render a platform session as a company one.

## Preconditions
- Live stack up; demo platform admin `super@ethera.ai` / `Sup3r-Dev-Only-2026!` seeded.
- Run-stamp namespace: none needed (read-only happy-path login; the demo admin is never mutated).

## Steps
1. POST `/platform/login` with the demo admin's correct credentials.
2. Assert 200, body keys are exactly `{access_token, refresh_token, token_type}`, `token_type=="bearer"`.
3. Assert no `user` field is present.
4. Decode the access token and confirm `aud='platform'`, `role='platform_admin'`, `org_id=None`.

## Expected result
200 with exactly the three token fields, `token_type='bearer'`, **no** `user` key; the access
token is a platform-audience JWT with `org_id=None`.

## Harness
Script: `harness/tc_010.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_010.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack, backend container against real uvicorn)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> 200 with exactly `{access_token, refresh_token, token_type}`. No `user` key present.
> Access token decodes to `aud='platform'`, `role='platform_admin'`, `org_id=None`.

**Evidence**

```
status=200
keys=['access_token', 'refresh_token', 'token_type']
token_type='bearer'
has_user_field=False
user_value='<<ABSENT>>'
access_token_len=343
refresh_token_len=64
access_aud='platform' access_role='platform_admin' access_org_id=None
VERDICT=PASS
```

**Verdict**

The defense held. The body is the exact 3-field contract; `user` is excluded by
`response_model_exclude_none=True` on the route (`platform_routes.py:46-48`), and the service
returns only the token pair (`platform_auth_service.py:96-98`). The access token is correctly
platform-scoped with no org. Confirms the PR-2 "Notes carried forward" live verification
(`/platform/login` excludes the null user field) holds on the running stack.

**Notes / follow-up**

Establishes the positive control for the rest of the PLOGIN suite (the `super@ethera.ai`
admin id is `609f2b17-bee9-4f7f-a26d-cb08f666497a`, reused as the identity anchor in TC-PC-015).
