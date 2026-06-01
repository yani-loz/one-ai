# TC-OL-040: Missing bearer → 401 (not 403) on all three new endpoints

| Field | Value |
|---|---|
| **ID** | TC-OL-040 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative / Authz |
| **Severity if it fails** | High (an unauthenticated request reaching a lifecycle mutation is a control breach) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
With NO `Authorization` header, `GET /platform/orgs/{id}`, `PATCH …/status`, and
`PATCH …/legal-hold` each return 401 — not the library default 403, and not a 422 from body
validation running first.

## Break hypothesis
The HTTPBearer scheme returns 403 (its default) instead of the contracted 401, or body
validation runs before the auth gate so a missing token yields 422 — masking the authz
failure.

## Preconditions
Live stack. Fresh run-stamped org (`contract40-<stamp>`) so the path id is real. VALID
bodies on the PATCHes so any 401 is purely the missing-auth gate, not body validation. No
Authorization header.

## Steps
1. `provision_company(prefix="contract40")` (to get a real org id).
2. `GET /platform/orgs/{id}` with no Authorization.
3. `PATCH …/status` `{status:"active"}` with no Authorization.
4. `PATCH …/legal-hold` `{legal_hold:false}` with no Authorization.
5. Assert each is 401.

## Expected result
401 with `{"detail":"Missing bearer token."}` for each.

## Harness
Script: `harness/tc_040.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_040.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All three endpoints returned 401 with "Missing bearer token." — never 403, never 422 — even
> though valid bodies were supplied. The auth gate fires before body validation.

**Evidence**

```
GET detail (no bearer)        -> 401 (expect 401) body={"detail":"Missing bearer token."}
PATCH status (no bearer)      -> 401 (expect 401) body={"detail":"Missing bearer token."}
PATCH legal-hold (no bearer)  -> 401 (expect 401) body={"detail":"Missing bearer token."}
```

**Verdict**

The defense held. `HTTPBearer(auto_error=False)` plus the explicit
`if credentials is None: raise TokenInvalidError("Missing bearer token.")` in
`get_current_platform_admin` (`backend/app/identity/dependencies.py:104-116`) converts the
library's default 403 into the contracted 401. Confirms the SPEC §4 "401 for no/invalid
token" posture on the new lifecycle routes.

**Notes / follow-up**

Org left untouched (no mutation succeeded — auth failed first). TC-OL-043 re-confirms the
gate is present on each route; TC-OL-041/042 cover invalid (vs missing) tokens.
