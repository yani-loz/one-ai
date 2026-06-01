# Target 02 — Findings & Recommendations Register (Platform Console backend)

> Dedicated tracking for what the 2026-06-01 dynamic adversarial pass surfaced. By decision,
> these are recorded **here in the testing tree**, not appended to `docs/FIX_BEFORE_PROD.md`.
> Full evidence + per-case detail: the consolidated audit
> [`docs/audits/2026-06-01_platform-console-dynamic-adversarial.md`](../../docs/audits/2026-06-01_platform-console-dynamic-adversarial.md)
> and the per-case files in this folder. Status legend: 🔴 open · 🟡 accepted/tracked-elsewhere · ✅ fixed · ⚪ info.

## Headline (the documented win, proven live)

| ID | Sev | Status | Finding | Evidence | Disposition |
|---|---|---|---|---|---|
| F-01 | Critical | 🟡 | **Forged dev-secret platform token = full platform takeover.** A token forged with the public dev `JWT_SECRET` + a random `sub` onboards a real org (TC-PC-024, 201, psql-verified) **and** lists all customers' metadata (TC-PC-025 / lead re-verify: **23 orgs**, 200). RLS inert + dev secret public ⇒ JWT secret is the single isolation layer. | `dependencies.py:103-117` (no admin-exists check), `database.py:32` (superuser conn) | Already tracked in `FIX_BEFORE_PROD.md`: *Rotate `JWT_SECRET`* (+ fail-boot-if-default) **and** *Enforce RLS*. This pass is the live proof both gates are real. Closing either de-fangs the read; closing both is the bar. Content-blindness bounds the read blast radius to existence + headcount (TC-PC-025). |

## New findings (all Low/Info — no live Critical/High defect)

| ID | Sev | Status | Finding | Recommended action |
|---|---|---|---|---|
| N-01 | Low | 🟡 | **bcrypt CPU-amplification DoS.** Every login (incl. unknown-email, via `DUMMY_PASSWORD_HASH`) runs full bcrypt `rounds=12`. 40 concurrent invalid logins → median **10.7 s**, all 401, no 500 (TC-PC-073, lead-re-verified invalid 322 ms ≈ valid 320 ms single-req). Unauthenticated attacker pins the bcrypt threadpool at zero cost. | **Per-IP throttle in *front* of bcrypt.** Do **not** remove the `DUMMY_PASSWORD_HASH` equalizer (it defends enumeration). The existing rate-limit deferral names only brute-force — its rationale should be widened to this vector. **→ Done 2026-06-01:** widened the rate-limit item in `FIX_BEFORE_PROD.md` (names the bcrypt-DoS vector + "throttle in front of bcrypt"; keeps `DUMMY_PASSWORD_HASH`). Throttle implementation still deferred. |
| N-02 | Info | 🟡 | **DB pool + worker sizing unconfigured.** `create_async_engine` sets no `pool_size` → SQLAlchemy defaults (5+10=**15** conns, `pool_timeout=30s`). Graceful to 200× concurrency today (no knee, TC-PC-071), but unsized and untracked; single-event-loop CPU is a co-bottleneck (latency grew ~10× faster than a pure pool-queue model). | Size the DB pool + uvicorn workers deliberately for the target load before prod. Capacity is currently accidental, not chosen. **→ Done 2026-06-01:** added a pool/worker-sizing item to `FIX_BEFORE_PROD.md`. Actual sizing still deferred (needs a load target). |
| N-03 | Low | 🟡 | **Logout ≠ token-family revocation under a race.** `logout` revokes only the presented hash (`token_rotator.py:66-68`); a descendant minted by a racing refresh survives (TC-PC-063, lead-re-verified: rotate R0→R1, logout(R0)=204, R1 refresh=200). | Same class as the tracked **AUD-06** reuse-family deferral — fold in (revoke the `subject_id+subject_type` family; needs the independent-commit / `audit_log` work AUD-06 already notes). |
| N-04 | Low | ✅ | **Login password field lacks `min_length`/byte bounds.** `PlatformLoginRequest.password` & `LoginRequest.password` are `Field(min_length=1, max_length=256)` — not `BcryptPassword` (8 chars + 72-byte cap) used by user-create. No live defect (verify swallows bcrypt's `ValueError` → 401, `password.py:51-54`), but a defense-in-depth asymmetry. *Corrected the test plan's wrong assumption that login uses `BcryptPassword`.* | Add a byte cap at the login schema boundary for parity (overlaps the *password policy* deferral). **→ FIXED 2026-06-01:** new `LoginPassword` type (1..128 chars, ≤72-byte cap) now backs `LoginRequest` + `PlatformLoginRequest`; an over-72-byte login is a clean 422. Regression: `test_login_overlong_password_returns_422`, `test_platform_login_overlong_password_returns_422`. |
| N-05 | Info | ⚪ | **Concurrent-issuance characterization.** 30 concurrent `/platform/login` → 30/30 200, all tokens + hashes distinct (TC-PC-064). No defect; recorded for completeness. | None. |

## What held (so it isn't re-litigated)

- **PC-02 ACs all proven live** (stronger than the cited unit tests): AC1/2/3a/3b/4/7/8 — see the
  README dashboard's coverage table. The PR-2 `test-1` false-green is **closed** (TC-PC-020/026 are
  discriminating: the audience guard is the sole reason for the 401).
- **Token-validation gate total** (PAZ 9/9): every malformed/forged/expired/missing-claim token → 401,
  never 403/500; gate on every endpoint (psql no-write proof).
- **Onboarding integrity** incl. **atomic rollback under a concurrent same-email race → zero orphans**
  (TC-PC-062, psql run-stamp-filtered). dup→409, fuzz (slug/pw-bytes/extra/NUL/injection/email-canon)
  all held.
- **Content-blindness** non-vacuous (`/orgs` 6 fields, `/me` 3, no hash). ⚠️ **Forward-fragile:** tenant-
  content columns don't exist yet — **re-run when the first content table lands.**
- **No availability defect:** zero 5xx / zero client errors across the whole STRESS phase; clean recovery,
  no connection leak (TC-PC-074).

## Capacity note (operationally relevant now)

Valid logins under concurrent load degrade *correctly but visibly*: **40 concurrent → 7.5 s median,
13 s max** (bcrypt threadpool serialization). Logins will *feel* broken under load until N-01 (throttle)
+ N-02 (pool/worker sizing) are addressed. Slow, never failing — but worth planning for.
