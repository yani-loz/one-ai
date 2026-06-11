| ID · Suite · Type · Mode | TC-IM-A06 · A (Connection plane & credential cipher) · Negative · http |
|---|---|
| Result ✅Pass · Tag — · Severity — (defense held; authz surface) · Status Executed | |

## Objective
Every connector route is gated: a **member** token → **403**; a **missing** token → **401**, on `create` / `list` / `disable`.

## Break hypothesis
A route missing `require_company_admin` would let a non-admin member configure/list/disable connections; a missing-token path defaulting to HTTPBearer's 403 (instead of 401) would mis-signal the auth state.

## Steps
1. With a `role="member"` token: `POST /connectors`, `GET /connectors`, `POST /connectors/{rand}/disable` → expect 403 each.
2. With no `Authorization` header: the same three → expect 401 each.

## Expected
Member → 403 on all three; no token → 401 on all three.

## Execution result (2026-06-09)
```
[PASS] a06_member_forbidden_403 :: create=403 list=403 disable=403
[PASS] a06_missing_token_unauthorized_401 :: create=401 list=401 disable=401
```
**Verdict:** ✅ Pass. Every route depends on `require_company_admin` (`connector_routes.py`), which enforces `role == company_admin` (member → `PermissionDeniedError` → 403) atop `get_current_principal` (missing token → `TokenInvalidError` → 401, via `auto_error=False`). **Tag:** — (positive/contract).
