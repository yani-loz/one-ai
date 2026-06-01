# TC-PC-011: No user-enumeration oracle on `/platform/login` (body + timing)

| Field | Value |
|---|---|
| **ID** | TC-PC-011 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | PLOGIN — Platform login negatives |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove that a wrong password for a **known** email and any password for an **unknown** email
are indistinguishable — same 401, byte-identical body, comparable response time — so an
attacker cannot enumerate which platform-admin emails exist.

## Break hypothesis
If the service short-circuited on `admin is None` (returning before running bcrypt), an
unknown email would answer measurably faster than a real-email-wrong-password (which pays a
full bcrypt verify). That timing delta is a classic enumeration oracle. A divergent error
body (e.g. "unknown account" vs "wrong password") would be an even cruder oracle.

## Preconditions
- Live stack up; demo platform admin seeded.
- Run-stamp namespace: unknown emails are minted as `nobody-plogin-{stamp()}@oneai.dev` so
  they are guaranteed non-existent and never collide with other runs. The demo admin is
  only ever sent a WRONG password (cannot succeed → never mutated).

## Steps
1. POST `/platform/login` with the demo email + a wrong password.
2. POST `/platform/login` with a run-stamped unknown email + the same wrong password.
3. Assert both → 401 and the response bodies are **byte-identical**.
4. Time 30 trials of each (fresh unknown email per trial) and report the medians + ratio.

## Expected result
Both → 401 with identical bodies; medians within ~2× (both pay exactly one bcrypt verify —
the real hash for the known email, `DUMMY_PASSWORD_HASH` for the unknown).

## Harness
Script: `harness/tc_011.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_011.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (live stack)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Wrong-password (known email) and unknown-email both return 401 with the identical body
> `{"detail":"Invalid email or password."}`. Median response times are essentially equal
> (0.319 s vs 0.316 s, ratio 0.991) — both pay one full bcrypt verify.

**Evidence**

```
wrong_pw_status=401 unknown_status=401
wrong_pw_body='{"detail":"Invalid email or password."}'
unknown_body='{"detail":"Invalid email or password."}'
bodies_byte_identical=True
median_wrong_pw_s=0.3192 median_unknown_email_s=0.3164 ratio=0.991
timings_comparable(0.5..2.0x)=True
VERDICT=PASS (timing_comparable=True)
```

**Verdict**

The defense held. `PlatformAuthService.login` (`platform_auth_service.py:82-88`) selects
`DUMMY_PASSWORD_HASH` when `admin is None` and **always** runs `verify_password` before the
combined `admin is None or not is_active or not password_ok` check — so the unknown-email
path performs the same bcrypt work (~0.31 s at rounds=12) and returns the same generic
`InvalidCredentialsError`. No enumeration oracle via body or timing. Confirms the
`DUMMY_PASSWORD_HASH` timing-equalizer defense (`security/password.py:60`) holds live.

**Notes / follow-up**

The ~0.31 s/login bcrypt cost is the same lever STRESS uses for `/platform/login` saturation.
Login is unthrottled (no rate limit) — tracked in `FIX_BEFORE_PROD.md` ("login rate-limiting
+ account lockout"); the enumeration defense here is orthogonal to and does not substitute for
that deferral.
