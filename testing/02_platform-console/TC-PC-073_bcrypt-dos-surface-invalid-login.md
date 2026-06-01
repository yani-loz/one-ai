<!--
  Test-case: STRESS suite — bcrypt DoS surface via 40 concurrent INVALID /platform/login.
-->

# TC-PC-073: bcrypt DoS surface — 40 concurrent INVALID /platform/login (unknown email)

| Field | Value |
|---|---|
| **ID** | TC-PC-073 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | STRESS — pool + bcrypt saturation |
| **Type** | Adversarial / Stress |
| **Severity if it fails** | Low / Info (CPU-amplification surface; anti-enumeration is correct) |
| **Status** | Executed |
| **Result** | ✅ Pass-with-concern |
| **Finding tag** | NEW (Info — unauthenticated bcrypt CPU-amplification, not in FIX_BEFORE_PROD) |

## Objective
Confirm that an **unauthenticated** attacker firing `POST /platform/login` with a junk
(unknown) email still pays the full bcrypt cost — because the service runs bcrypt against a
`DUMMY_PASSWORD_HASH` to make an unknown email timing-indistinguishable from a real one
(anti-enumeration). Verify (1) all → 401 with the generic error, and (2) latency is
comparable to the VALID-login burst (TC-PC-072) — i.e. the timing equalizer works. **Record
the dual-edged property:** the same equalizer is also a CPU-amplification DoS surface, since
junk credentials pin bcrypt worker threads at zero authentication cost to the attacker.

## Break hypothesis
Two failure shapes hunted:
1. **Enumeration oracle** — if invalid logins were *faster* than valid ones (because the code
   skipped bcrypt on an unknown email), an attacker could distinguish "email exists" from
   "email unknown" by response time. A FAIL-for-security would be invalid-login latency
   markedly **lower** than the valid-login baseline.
2. **Availability** — any 500 under the invalid burst.
PASS = all 401 (generic body) with latency **comparable** to TC-PC-072's valid burst,
proving the `DUMMY_PASSWORD_HASH` equalizer fires. The PASS-WITH-CONCERN caveat is the
unavoidable corollary: that equalizer is a CPU sink an unauthenticated attacker can drive.

## Preconditions
- Live stack healthy. The unknown email is run-stamped (STRESS suite) so it can never match a
  real account: `stress-nobody-{stamp()}@nonexistent.invalid`.
- Demo admin untouched (we never present its email here). Wrong password against an unknown
  email exercises the `admin is None → DUMMY_PASSWORD_HASH` branch in `login()`.
- Client timeout 120s. Compared head-to-head against TC-PC-072's valid-burst median.

## Steps
1. Capture a single invalid-login latency baseline (unknown email).
2. Fire 40 concurrent `POST /platform/login` with the unknown email + a junk password.
3. `summarize()` — assert all 401, zero 500, zero EXC. Report min/median/max latency.
4. Compare the median against TC-PC-072's valid-burst median (the equalizer check) and assert
   the response body is the generic invalid-credentials error (no email echoed, no detail).

## Expected result
All 40 → **401** with `{"detail": "Invalid email or password."}` (or the API's generic
shape). Latency **comparable** to the valid-login burst (within the same order of magnitude —
the bcrypt-against-dummy-hash cost is paid). Zero 500, zero EXC.

## Harness
Script: `harness/tc_073.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_073.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** NEW (Info)

> **Harness note (fixed before recording):** the first run used `@nonexistent.invalid`,
> which Pydantic `EmailStr`/`NormalizedEmail` rejects as a reserved TLD with **422 BEFORE the
> login service runs** — so bcrypt was never reached (median ~174ms, no cost paid). That was a
> harness bug, not a finding. Fixed by using a validator-accepted domain (`@oneai.dev`) with a
> run-stamped unknown local-part, which passes validation and reaches the bcrypt 401 path. The
> result below is the corrected run.

**Actual behavior**

> All 40 concurrent invalid logins (unknown email, junk password) returned 401 with the
> generic `{"detail": "Invalid email or password."}` body — no email echoed, no detail
> distinguishing "unknown email" from "wrong password". Zero 500, zero EXC. The latency
> profile was the SAME order of magnitude as the valid-login burst (TC-PC-072): median ~10.7s
> (this case) vs ~7.5s valid — both squarely bcrypt-bound (7–13s range), and the single-request
> baselines match closely (~367ms invalid vs ~328ms valid). This proves the
> `DUMMY_PASSWORD_HASH` timing-equalizer fires — an unknown email pays the full bcrypt cost
> and is timing-indistinguishable from a real one, closing the enumeration oracle. The concern
> is the corollary: an UNAUTHENTICATED attacker drove the bcrypt threadpool to a ~10s median
> with pure junk, pinning worker threads at zero authentication cost.

**Evidence**

```
unknown email: stress-nobody-19e8283c337b5ae@oneai.dev
baseline single invalid /platform/login: 401  367.4ms   body={'detail': 'Invalid email or password.'}
fired 40 concurrent invalid POST /platform/login (client timeout=120s)
status tally: {401: 40}
latency ms  -> min=438.1  median=10721.9  max=12909.3
500 count: 0   client-EXC count: 0
sample body: {'detail': 'Invalid email or password.'}

equalizer comparison (median):
  invalid burst (this case): ~10721.9 ms   (single-req baseline 367ms)
  valid   burst (TC-PC-072): ~7517.2 ms    (single-req baseline 328ms)
  -> same order of magnitude (both 7-13s, bcrypt-bound; baselines ~match 367 vs 328ms):
     DUMMY_PASSWORD_HASH pays full bcrypt cost on an unknown email (the equalizer
     PREREQUISITE). The byte-identical known-bad vs unknown-bad enumeration test is PLOGIN.
```

**Verdict**

Two findings, both correct-by-design but one with a caveat:

1. **Timing-equalizer prerequisite holds (PASS).** `platform_auth_service.py:82-88 login()`
   fetches the admin, then *unconditionally* runs `verify_password` against the real hash OR
   `DUMMY_PASSWORD_HASH` before deciding, so an unknown email pays the **full bcrypt cost** and
   returns the identical generic 401. The invalid-burst median (~10.7s) is the same order of
   magnitude as the valid-burst median (~7.5s) — both bcrypt-bound in the 7–13s range — and the
   single-request baselines match closely (~367ms invalid vs ~328ms valid). That proves the
   *prerequisite* for anti-enumeration: junk emails are not cheaper than real ones. The full
   byte-identical known-bad-vs-unknown-bad enumeration property is a functional negative owned
   by the PLOGIN suite (cross-referenced), not re-proven here. No timing oracle visible; no 500.

2. **CPU-amplification DoS surface (CONCERN — NEW Info).** The very mechanism that defeats
   enumeration means an *unauthenticated* attacker can pin the bcrypt worker threads (rounds=12)
   with arbitrary junk credentials — every junk POST costs the server a full bcrypt verify but
   costs the attacker nothing. At 40 concurrency the median already hit ~7.4s; sustained junk
   traffic would starve legitimate logins (which share the same threadpool). This is a
   classic bcrypt-login amplification surface. `FIX_BEFORE_PROD.md` tracks **login
   rate-limiting + lockout** under Auth hardening, but frames it as a *credential-stuffing /
   brute-force* control — it does **not** name the bcrypt-CPU-amplification / DoS angle, and
   nothing tracks the unauthenticated-CPU-sink explicitly. Hence NEW (Info): the rate-limit
   item's *rationale* should be widened to include "unauthenticated CPU-amplification via the
   anti-enumeration dummy-hash path," since rate-limiting (per-IP, before bcrypt) is precisely
   the mitigation for both.

**Notes / follow-up**

Severity Info — the equalizer is the *correct* security choice (do not remove it). The right
mitigation is a cheap per-IP rate-limit/throttle **in front of** the bcrypt call so junk
traffic is shed before it reaches the threadpool — which is the already-tracked login
rate-limiting item, just with this additional justification. Cross-ref TC-PC-072 (valid
baseline) and the PLOGIN suite (enumeration negatives at the functional level).
