# TC-FE-002: Access token never in storage; company refresh is the only persisted token

| Field | Value |
|---|---|
| **ID** | TC-FE-002 |
| **Target** | Frontend (Platform Console + auth client) |
| **Suite** | Token storage |
| **Type** | Adversarial |
| **Severity if it fails** | High (access-token theft via XSS) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
The access JWT must never touch web storage (memory only, both domains). The **company** refresh token
*is* persisted (deliberate SPA trade-off, tracked in `FIX_BEFORE_PROD.md` → move to httpOnly cookie).

## Break hypothesis
The access JWT (or a second copy of the refresh token) is found in `localStorage`/`sessionStorage`.

## Preconditions
Company admin `admin@demo.oneai`, storage clean.

## Steps
1. Company login → `/`.
2. Dump storage; check `oneai.refresh_token` present and is an **opaque** string (not a JWT); scan for any JWT value.

## Expected result
`localStorage["oneai.refresh_token"]` present (opaque, ~64 chars); no JWT anywhere in storage.

## Harness
Playwright MCP `browser_evaluate`.

---

## Execution result

- **Run at:** 2026-06-01 ~11:00 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-DOCUMENTED (company refresh persistence is the tracked httpOnly-cookie deferral)

**Actual behavior**
> Company login persisted exactly one token — the opaque refresh token — and no access JWT.

**Evidence**
```
url: /
localStorage_keys: ["oneai.refresh_token"]
refresh_token_present: true  len: 64  is_jwt: false  sample: "VuP9oZkaQOjp…"
any_access_jwt_in_storage: []   sessionStorage_keys: []   cookies: ""
```

**Verdict**
Defense held. The access token lives in `accessTokenInMemory` (never persisted). The company refresh
token is the single persisted credential — opaque (sha256-stored server-side), not a JWT. This XSS
exposure is the deliberate SPA trade-off already tracked (*Move the refresh token to an httpOnly cookie*),
hence CONFIRMS-DOCUMENTED, not a new finding. Contrast TC-FE-001 (platform persists nothing).

**Notes / follow-up**
Closing the tracked httpOnly-cookie item removes the company-side exposure too.
