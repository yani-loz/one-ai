# TC-PC-008: Unknown admin — forged valid-signature platform token (random sub) → 401

| Field | Value |
|---|---|
| **ID** | TC-PC-008 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PSES — Session lifecycle |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-02-AC7 (unknown admin): a token that is **cryptographically valid** (correct dev
secret, `aud='platform'`, unexpired, well-formed claims) but whose `sub` references **no
platform_admins row** is rejected at `/platform/me` with 401 — not 200, not 500.

## Break hypothesis
Because RLS is inert and the dev JWT secret is the forgeable default, an attacker can mint a
perfectly-signed platform token for any `sub`. The bet: a random-`sub` token reaches
`build_admin_view_by_id`, the DB lookup returns None, and instead of a clean 401 the code 500s
(unhandled None) or — worse — returns 200 with a partial/empty identity.

## Preconditions
- Live stack; dev JWT secret is the forgeable default (`DEV_SECRET`). PSES suite; no orgs created.
- `forge_platform_token()` with a random `sub` (uuid4) — touches no real admin row.

## Steps
1. `forge_platform_token(sub=str(uuid4()))` (valid signature + `aud='platform'`).
2. Locally `jwt.decode` it to confirm it IS a structurally valid platform token (the guard,
   not malformation, must be what rejects it).
3. `GET /platform/me` with the forged token → expect 401.

## Expected result
`GET /platform/me` → `401` (the forged-but-unknown admin is rejected at the DB-resolution step).

## Harness
Script: `harness/tc_008.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_008.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The forged token decoded cleanly (valid signature, `aud=platform`), proving it passes the
> token-verification layer. `GET /platform/me` then returned 401 (not 200, not 500) because no
> platform_admins row matches the random `sub`.

**Evidence**

```
FORGED-SUB: 3b4c91e9-3a14-43da-9550-ba0aa0f75831
DECODE-OK aud= platform sub= 3b4c91e9-3a14-43da-9550-ba0aa0f75831
GHOST /platform/me STATUS: 401 BODY: {'detail': 'Invalid email or password.'}
UNKNOWN-ADMIN-REJECTED: True
```

**Verdict**

Defense held. The token clears `get_current_platform_admin` (signature + aud + expiry all valid),
so the rejection is the *second* layer: `build_admin_view_by_id` finds no row and raises
`InvalidCredentialsError` → 401 (`platform_auth_service.py:136-138`). A valid signature alone is
NOT sufficient to be served — the admin must exist. PC-02-AC7 (unknown admin) confirmed live,
including under the real forged-token capability (inert RLS + forgeable dev secret).

**Notes / follow-up**

The discriminating cross-domain forged-token case (company-aud token bearing a REAL platform
admin id) is the XDOM suite's; this case isolates the unknown-`sub` path within the platform
domain.
