# TC-OL-042: Expired / malformed-sub token → 401, never 500

| Field | Value |
|---|---|
| **ID** | TC-OL-042 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Adversarial / Authz |
| **Severity if it fails** | High (a 500 on a crafted token is info-disclosure + a fail-open risk) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
On `PATCH …/status`: an EXPIRED (but otherwise valid dev-secret) platform token → 401; a
valid-signature token whose `sub` is not a UUID → 401 — never a 500 from an unhandled
parse error.

## Break hypothesis
The expired token is accepted (expiry not enforced), or the malformed `sub` reaches
`UUID(...)` unguarded and raises a ValueError → 500 (fail-open / info disclosure).

## Preconditions
Live stack. MY real org id (`contract42-<stamp>`) + a valid body, so any 401 isolates to the
token. Two forged tokens: `expired=True`, and `sub='not-a-uuid'` (both dev-secret signed so
the signature itself is valid — the failure must come from expiry / claim parsing).

## Steps
1. `provision_company(prefix="contract42")` (real org id).
2. Forge an expired platform token; `PATCH …/status {status:"active"}`.
3. Forge a platform token with `sub='not-a-uuid'`; `PATCH …/status {status:"active"}`.
4. Assert each is 401, never 500.

## Expected result
401 (`Access token has expired.` / `Access token claims are malformed.`); never 500.

## Harness
Script: `harness/tc_042.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_042.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The expired token returned 401 "Access token has expired."; the non-UUID `sub` token
> returned 401 "Access token claims are malformed." — neither produced a 500, and neither
> reached the org row (no mutation).

**Evidence**

```
PATCH status with EXPIRED token        -> 401 (expect 401, never 500) body={"detail":"Access token has expired."}
PATCH status with sub='not-a-uuid'     -> 401 (expect 401, never 500) body={"detail":"Access token claims are malformed."}
```

**Verdict**

The defense held. Expiry is enforced by `decode_access_token` (→ `TokenExpiredError` → 401).
The malformed `sub` is caught by `_principal_from_claims`
(`backend/app/identity/dependencies.py:55-70`), which wraps `UUID(...)` in a try/except and
raises `TokenInvalidError` ("claims are malformed") instead of letting the ValueError become
a 500. Confirms the audit's claim that malformed claims yield 401, never a 500.

**Notes / follow-up**

Org left `active`, untouched (auth failed before the write). Complements TC-OL-041
(none-alg / wrong-secret).
