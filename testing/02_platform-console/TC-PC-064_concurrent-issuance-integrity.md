# TC-PC-064: Concurrent issuance integrity (distinct tokens, no overwrite)

| Field | Value |
|---|---|
| **ID** | TC-PC-064 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | RACE — Concurrency races |
| **Type** | Concurrency |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | NEW (fresh concurrency observation — no prior-audit fix to confirm) |

## Objective
Prove that 30 simultaneous `POST /platform/login` for the same admin each succeed (200) and
each receive a **distinct** access + refresh token — no collision, no shared-state overwrite
of an in-flight issued token under concurrent issuance.

## Break hypothesis
Token generation uses `secrets.token_urlsafe` (cryptographically distinct), so a true
collision is astronomically unlikely. What this catches is the realistic failure: a
shared-mutable-state bug in the issuer/session (e.g. a reused session object, a module-level
buffer, or a UNIQUE-violation on `refresh_tokens.token_hash` insert under load) surfacing as a
**non-200** (500/EXC) or a duplicated/overwritten token row. Any non-200 or duplicate is the
win.

## Preconditions
- Live stack up; demo platform admin `super@ethera.ai` (read-only login; never mutated).
- HTTP-layer proof: distinctness asserted on the 30 returned tokens and their sha256 hashes.

## Steps
1. Fire **30** concurrent `POST /platform/login` for the demo admin.
2. `summarize()` the tally; assert all 200.
3. Collect the 30 refresh and 30 access tokens; assert each set has 30 distinct values.
4. Cross-check: the 30 refresh-token sha256 hashes (the DB storage form) are all distinct.

## Expected result
30 → 200; 30 distinct refresh tokens; 30 distinct access tokens; 30 distinct refresh hashes;
zero 500/EXC.

## Harness
Script: `harness/tc_064.py` · run: `docker compose exec -T backend python - < testing/02_platform-console/harness/tc_064.py`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** NEW (fresh concurrency observation — no prior-audit fix to confirm)

**Actual behavior**

> 30 concurrent logins all returned 200, each with a unique access and refresh token; all 30
> refresh-token hashes (the DB form) were distinct. No 500, no client EXC.

**Evidence**

```
TALLY: {200: 30}
COUNTS  200: 30  500: 0  EXC: {}
ISSUED PAIRS: 30  DISTINCT refresh: 30  DISTINCT access: 30
SAMPLE 200 BODY keys: ['access_token', 'refresh_token', 'token_type']
SAMPLE refresh prefix: 2UaM9x1s ...
DISTINCT refresh-hashes: 30 of 30
VERDICT: PASS — 30/30 200, all refresh+access tokens distinct
```

**Verdict**

The defense held. Concurrent issuance is integrity-preserving: every login got its own pair,
no two requests shared or overwrote a token, and the `refresh_tokens.token_hash` UNIQUE
inserts all succeeded under load (no 500). Token generation is `new_refresh_token` →
`secrets.token_urlsafe(48)` (`backend/app/identity/security/tokens.py:91-98`) and the JWT
`jti` is a fresh `uuid4` per encode (`:58`), so concurrent issuance has no shared mutable
state to corrupt. Low find-probability case — re-proved live once. Tagged NEW: this is a
fresh concurrency characterization (no specific prior-audit fix corresponds to "concurrent
issuance distinctness"), and it found no defect.

**Notes / follow-up**

The distinctness floor is cryptographic, not a server invariant per se; the load value here
is confirming no 500/overwrite under the `bcrypt` + DB-insert path firing 30× at once.
