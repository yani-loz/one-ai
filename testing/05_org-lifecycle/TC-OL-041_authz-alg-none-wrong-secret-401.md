# TC-OL-041: Forged alg=none / wrong-secret token → 401

| Field | Value |
|---|---|
| **ID** | TC-OL-041 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Adversarial / Authz |
| **Severity if it fails** | Critical (alg=none or wrong-secret acceptance = full token forgery, every org compromised) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A forged platform token signed with `alg='none'` (no signature) and one signed with the
WRONG secret are both rejected with 401 on `GET /platform/orgs/{id}` — the signature
verification is the isolation layer (RLS is inert; JWT secrecy is everything).

## Break hypothesis
The decoder accepts `alg='none'` (the classic JWT bypass) or fails open on a bad signature,
letting an attacker mint platform tokens at will. A 200 here = catastrophic forgery.

## Preconditions
Live stack. MY real org id (`contract41-<stamp>`) as the target so a 401 can only come from
the token, never a 404. Two forged tokens: `alg=none`, and HS256 with a non-dev secret.

## Steps
1. `provision_company(prefix="contract41")` (real org id).
2. Forge a platform token with `alg='none'`; `GET /platform/orgs/{id}`.
3. Forge a platform token signed with a wrong secret; `GET /platform/orgs/{id}`.
4. Assert each is 401.

## Expected result
401 (`Access token is invalid.`) for both — never 200.

## Harness
Script: `harness/tc_041.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_041.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both the alg=none token and the wrong-secret token were rejected with 401 "Access token is
> invalid." against a real org id — the classic alg=none bypass does not work, and a bad
> signature fails closed.

**Evidence**

```
GET detail with alg=none token      -> 401 (expect 401) body={"detail":"Access token is invalid."}
GET detail with wrong-secret token  -> 401 (expect 401) body={"detail":"Access token is invalid."}
```

**Verdict**

The defense held. `decode_access_token` pins the HS256 algorithm and verifies the signature
against the configured secret; a `none`-alg or wrong-secret token raises `TokenInvalidError`
(→ 401, `error_handlers.py:38`). Because the target was a real org, the 401 isolates to the
token, not a 404. Confirms the JWT-secrecy-is-the-isolation-layer posture (the dev secret
itself is the documented forgeable default — see TC for forged-dev-secret DoS / FIX_BEFORE_PROD).

**Notes / follow-up**

This proves wrong-key/none-alg forgery is blocked; the *dev-secret* forgery capability
(separate, documented) is the residual risk tracked in FIX_BEFORE_PROD "Rotate JWT_SECRET".
Org left untouched.
