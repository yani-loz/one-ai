# TC-PC-065: Rotation-chain integrity (single-use holds end-to-end)

| Field | Value |
|---|---|
| **ID** | TC-PC-065 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | RACE — Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that single-use rotation holds across a long chain: after rotating a refresh token 20×,
every one of the 20 prior tokens is dead (401) and only the final tip token still works (200).
No ancestor in the chain can be resurrected.

## Break hypothesis
Each rotation must revoke the consumed token before issuing the next. If revocation were
skipped, deferred, or keyed wrong (e.g. only the immediately-prior token revoked, or a
stale-cache making an older ancestor re-validate), then replaying an earlier ancestor would
return **200** — a reused refresh token forking a parallel live session, defeating single-use
over time. Any prior returning non-401 (a "resurrected ancestor") is the win.

## Preconditions
- Live stack up; demo platform admin (one initial login; the chain is built from successive
  responses).
- HTTP-layer proof: the chain's 21 tokens (1 original + 20 rotations) tracked in order;
  invariants asserted on replay status codes.

## Steps
1. `POST /platform/login` → token `t0`.
2. Rotate serially 20×: `t1 = refresh(t0)`, …, `t20 = refresh(t19)` — each must be 200.
3. Replay every prior token `t0…t19` → each MUST be 401.
4. Replay the tip `t20` → MUST be 200 (consumes it, issuing t21 — fine).

## Expected result
Chain reaches length 21; all 20 priors → 401; tip → 200; no resurrected ancestor.

## Harness
Script: `harness/tc_065.py` · run: `docker compose exec -T backend python - < testing/02_platform-console/harness/tc_065.py`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> A 20-step rotation chain built cleanly (length 21 including the original). Replaying all 20
> ancestor tokens returned 401 for every one; the final tip token still rotated (200). No
> ancestor resurrected.

**Evidence**

```
CHAIN LENGTH (incl. original): 21
PRIOR (must-be-dead) tokens: 20  FINAL (must-live) token: 1
PRIOR REPLAY CODES (all should be 401): {401: 20}
FINAL TOKEN rotate -> 200  body keys: ['access_token', 'refresh_token', 'token_type']
ALL PRIORS DEAD (401): True  FINAL LIVES (200): True
VERDICT: PASS — single-use chain holds; only the tip is live
```

**Verdict**

The defense held end-to-end. Every rotation revokes the consumed token at
`backend/app/identity/services/token_rotator.py:62` (conditional `UPDATE ... WHERE revoked_at
IS NULL`) before `PlatformAuthService.refresh` issues the next pair
(`backend/app/identity/services/platform_auth_service.py:107-120`). Each ancestor's
`revoked_at` is set, so on replay `consume` either finds it revoked (`rowcount == 0` →
`RefreshTokenInvalidError` → 401). No caching or stale read lets an older token re-validate —
the lookup is a fresh `get_by_hash` per request. Confirms single-use (AUD-01 / PC-02-AC4)
holds across a long serial chain, not just a single rotation.

**Notes / follow-up**

Complements TC-PC-060 (single-use under same-row concurrency) by proving the temporal /
sequential dimension. Note: AUD-06 (reuse-family revocation) remains a documented deferral —
this test does NOT assert that replaying a consumed ancestor revokes the live descendant
chain; it only asserts the ancestor itself is dead. That family-revocation gap is tracked in
`docs/FIX_BEFORE_PROD.md` and is out of scope here.
