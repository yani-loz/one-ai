<!-- PAZ suite — Platform token-validation matrix (401 not 403/500). -->

# TC-PC-032: Tampered signature on `GET /platform/me` → 401 (discriminating control)

| Field | Value |
|---|---|
| **ID** | TC-PC-032 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PAZ — Platform token-validation matrix |
| **Type** | Adversarial |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove signature integrity is enforced: a valid platform token with **one character flipped in the
signature segment** is rejected, while the **untouched** token (same claims) authenticates — so the
signature, not some other claim, is the deciding factor (a discriminating control).

## Break hypothesis
If signature verification were skipped or lenient, flipping a signature byte would still
authenticate. The bet: the tampered token still returns 200 (signature not actually checked).

## Preconditions
- Live stack up. Valid token forged with the **real** demo admin sub
  (`609f2b17-bee9-4f7f-a26d-cb08f666497a`) so the control hits 200; only the signature is mutated.
- No org/email created; demo admin untouched (read-only `/me`).

## Steps
1. Forge a valid platform token; `GET /platform/me` with it → expect **200** (control).
2. Flip the first char of the signature segment to a guaranteed-different char.
3. `GET /platform/me` with the tampered token → expect **401**.

## Expected result
- Control: **200** `{id,email,full_name}`.
- Tampered: **401** `{"detail": "Access token is invalid."}` (signature mismatch).
- The 200→401 delta proves the signature is the discriminator, never 500.

## Harness
Script: `harness/tc_032.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_032.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The untouched token authenticated (200, real identity). Flipping the signature's first char
> from `M`→`A` flipped the outcome to **401**. Signature verification is enforced and is the sole
> variable between the two requests.

**Evidence**

```
CONTROL valid token /platform/me -> 200 {"id":"609f2b17-bee9-4f7f-a26d-cb08f666497a","email":"super@ethera.ai","full_name":"Ethera Super Admin"}
sig first char: 'M' -> 'A'
TAMPERED-SIG /platform/me -> 401 {"detail":"Access token is invalid."}
assert_control200_and_tampered401: PASS
```

**Verdict**
Defense held, discriminatingly. Code path: `decode_access_token`
(`backend/app/identity/security/tokens.py:77-88`) — `jwt.decode` recomputes the HMAC over the
header+payload and raises `InvalidSignatureError ⊂ InvalidTokenError` → `TokenInvalidError` → 401.
Confirms the JWT-secrecy isolation model the audits rely on (RLS inert).

**Notes / follow-up**
This is the integrity counterpart to TC-PC-034 (wrong secret produces the same 401 via a
different internal cause). Both confirm an attacker cannot mint or mutate a token without the
secret.
