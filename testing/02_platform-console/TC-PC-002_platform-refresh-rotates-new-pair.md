# TC-PC-002: POST /platform/refresh rotates to a brand-new pair (both tokens differ)

| Field | Value |
|---|---|
| **ID** | TC-PC-002 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Positive |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC1: `POST /platform/refresh` with a valid platform refresh token returns a
**brand-new** `{access_token, refresh_token, token_type}` pair where BOTH tokens differ from
the originals, and the body excludes the null `user` field.

## Break hypothesis
A violation = the endpoint returns the SAME refresh token (no rotation), or omits/changes
`token_type`, or leaks a `user` field, or returns a stale access token.

## Preconditions
- Live stack; demo platform admin seeded. PSES suite; no orgs created.

## Steps
1. `platform_login_pair()` → (old_access, old_refresh).
2. `POST /platform/refresh` with `{refresh_token: old_refresh}`.
3. Assert 200; key set == `{access_token,refresh_token,token_type}`; `token_type=="bearer"`;
   no `user`; new_access != old_access; new_refresh != old_refresh.

## Expected result
`200`; exactly the 3 token fields; both tokens are new; no `user` field.

## Harness
Script: `harness/tc_002.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_002.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Refresh returned 200 with a fresh access+refresh pair (both different from the originals),
> `token_type=="bearer"`, exactly the three token fields, and no `user` field.

**Evidence**

```
STATUS: 200
KEYS  : ['access_token', 'refresh_token', 'token_type']
EXACT-3-FIELDS: True
TOKEN-TYPE: bearer
NO-USER-FIELD: True
ACCESS-DIFFERS: True
REFRESH-DIFFERS: True
```

**Verdict**

Defense held. `PlatformAuthService.refresh` (`platform_auth_service.py:100-120`) consumes the
old token then `issue_pair`s a new one; the route uses `response_model_exclude_none=True`
(`platform_routes.py:62-64`) so the null `user` is dropped. PC-02-AC1 confirmed live.

**Notes / follow-up**

Single-use property of the OLD token is proven separately in TC-PC-003.
