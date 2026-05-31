<!--
  TC-IA-016 — no hash leak in login responses + best-effort constant-time timing sanity.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-016: No password/hash leak in login responses + best-effort constant-time timing sanity

| Field | Value |
|---|---|
| **ID** | TC-IA-016 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Adversarial |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | NA |

## Objective
(1) Assert NO password / hash / token-hash field appears anywhere in any `/auth/login`
response (success or failure). (2) Best-effort timing sanity: time ~20 logins for a
KNOWN-existing email + wrong password vs a NEVER-existing email; report mean/stdev. The
dummy-hash path should make the two comparable (no timing enumeration oracle). Inconclusive
timing is acceptable and is NOT a hard proof.

## Break hypothesis
(1) A success or failure body echoes the stored `password_hash` / submitted password / a
refresh-token hash. (2) The unknown-email path is measurably FASTER (skips bcrypt) than the
wrong-password path — a timing oracle to enumerate registered emails. We expect comparable
means because `auth_service.login` runs `verify_password` against `DUMMY_PASSWORD_HASH` when
no user matched.

## Preconditions
Live stack. Harness onboards a fresh org `authn016-<stamp>` (real admin). Part 1: scan the
success body and a failure body for secret-like keys (password/hash/secret/salt) AND for a
bcrypt hash marker (`$2b$`/`$2a$`) appearing anywhere in the raw body text — a stored hash
always carries that prefix, so its absence proves no hash leaked. Part 2: 5 warmup + 20
timed logins each for {real email + wrong pw} and {ghost email + any pw}; report mean /
stdev / median. Timing over the network + shared container is noisy; the verdict is
directional, not cryptographic proof.

## Steps
1. Onboard org + admin.
2. Login success scan + failure scan → assert no secret keys, no `$2b$`/`$2a$` bcrypt
   marker anywhere in either body.
3. Warm up, then time 20× login(real,wrong) and 20× login(ghost,wrong).
4. Report mean/stdev/median for both; compute the delta.

## Expected result
- No `password`/`hash`/`secret` key in any body; the DB hash never appears as a substring.
- Means for the two failure paths are of the SAME order (within normal jitter). A large,
  consistent gap (unknown ≪ wrong-pw) would indicate a timing oracle.

## Harness
Script: `harness/tc_016.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_016.py`

---

## Execution result

- **Run at:** 2026-05-31 12:04 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** NA

**Actual behavior**

> No secret/hash field appears in any login response, and no bcrypt hash marker
> (`$2b$`/`$2a$`) shows up in any body. Timing: the unknown-email path (mean 628 ms) is
> actually marginally SLOWER than the wrong-password path (mean 565 ms) — ratio 1.11x, both
> dominated by one bcrypt verify — so there is no "unknown email is faster" timing oracle;
> the dummy-hash equalizer holds. The ~63 ms delta is well within the per-call stdev
> (~220-250 ms) of a shared dev container and is NOT a hard cryptographic constant-time
> proof — hence Pass-with-concern, tag NA.

**Evidence**

```
== onboard == 201 admin=admin-authn016-19e7d32ee293ed9@oneai.dev
-- leak scan --
success body secret keys: NONE
failure body secret keys: NONE
bcrypt hash marker ($2b$/$2a$) present in any body: False
-- timing (20 samples each, after 5 warmup) --
real-email + WRONG password : mean=564.75 ms  median=589.44 ms  stdev=221.89 ms
ghost-email (never-existing): mean=628.08 ms  median=673.44 ms  stdev=247.50 ms
delta(mean) = 63.33 ms  ratio = 1.11x  (same order of magnitude: True)
RESULT: PASS-WITH-CONCERN (no leak; timing comparable but not a hard proof)
```

**Verdict**

Defense held on the hard axis (no hash/secret leak — `auth_schemas.py:53-63` exposes only
tokens + the non-secret user view). On the timing axis, the dummy-hash equalizer
(`security/password.py:60`, used in `auth_service.py:65`) keeps the unknown-email path from
short-circuiting bcrypt, so the two failure paths are the same order of magnitude — no
practical timing oracle observed. Marked Pass-with-concern / NA because a noisy 20-sample
network measurement is a sanity check, not a statistically rigorous constant-time proof.

**Notes / follow-up**

A rigorous constant-time verification would need in-process, high-resolution timing with
thousands of samples and outlier trimming — out of scope for a black-box harness. The
response-content enumeration proof is the stronger guarantee (TC-IA-012).
