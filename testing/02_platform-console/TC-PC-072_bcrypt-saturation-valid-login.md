<!--
  Test-case: STRESS suite — bcrypt saturation via 40 concurrent VALID /platform/login.
-->

# TC-PC-072: bcrypt saturation — 40 concurrent VALID /platform/login

| Field | Value |
|---|---|
| **ID** | TC-PC-072 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | STRESS — pool + bcrypt saturation |
| **Type** | Concurrency / Stress |
| **Severity if it fails** | Medium (a 500 under login load is an availability defect) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — (NA — pure positive/stress test; logins serialise but never error, no prior fix re-proven) |

## Objective
Prove that `POST /platform/login` under a burst of valid logins serialises on the bcrypt
work (rounds=12, run in the anyio worker threadpool) — latency balloons but every request
returns 200 with a token pair, never a 500. Establishes the VALID-login latency baseline that
TC-PC-073 (invalid-login DoS surface) is compared against.

## Break hypothesis
bcrypt rounds=12 is intentionally expensive (~tens of ms of pure CPU per verify). The anyio
threadpool that runs `verify_password` has a bounded worker count (default 40), so 40
concurrent valid logins contend for both the threadpool AND the 15-conn DB pool (the session
is held across the bcrypt call). If anything is mis-wired, the contention surfaces as a 500
(threadpool exhaustion / pool timeout) or a client EXC. **A FAIL is any 500.** PASS is all
40 → 200 with a wide latency spread (the serialisation signature).

## Preconditions
- Live stack healthy. bcrypt cost factor is rounds=12 (the platform admin's stored hash).
- Uses the demo platform admin's **valid** credentials. Login is read-only w.r.t. the admin
  row (no mutation) — the demo admin is never modified, only authenticated. Each login DOES
  insert a refresh-token row (rotation store); those are demo-admin-scoped and harmless.
- Client timeout 120s so bcrypt-induced latency is never mistaken for a client deadline.

## Steps
1. Capture a single valid-login latency baseline.
2. Fire 40 concurrent `POST /platform/login` with the demo admin's correct password, each
   timed with wall-clock `perf_counter`.
3. `summarize()` statuses; report min/median/max latency. Assert all 200, zero 500.

## Expected result
All 40 → **200**, each body = `{access_token, refresh_token, token_type}`. Latency balloons
vs the baseline (logins serialise on the bounded bcrypt threadpool) but stays bounded — no
500, no `EXC:*`.

## Harness
Script: `harness/tc_072.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_072.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** — (NA — pure positive/stress test; no prior audit fix re-proven)

**Actual behavior**

> All 40 concurrent valid logins returned 200 with a full `{access_token, refresh_token,
> token_type}` body. Zero 500, zero client EXC. Latency ballooned dramatically vs the ~328ms
> single-login baseline — median ~7.5s, max ~13.2s — the unmistakable signature of bcrypt
> rounds=12 verifications serialising through the bounded anyio worker threadpool. The server
> degraded gracefully (slow, never failing).

**Evidence**

```
baseline single valid /platform/login: 200  328.4ms
fired 40 concurrent valid POST /platform/login (client timeout=120s)
status tally: {200: 40}
latency ms  -> min=385.9  median=7517.2  max=13201.8
500 count: 0   client-EXC count: 0
sample body keys: ['access_token', 'refresh_token', 'token_type']
```

**Verdict**

The defense held. `POST /platform/login` (`platform_routes.py:49` →
`platform_auth_service.py:75 login` → `security/password.py verify_password`) runs bcrypt
rounds=12 in the anyio threadpool; 40 concurrent valid logins serialised on that bounded pool,
inflating median latency ~23× over the ~328ms baseline (to ~7.5s, worst ~13.2s) but every
request completed with a valid token pair. No 500, no pool/threadpool exhaustion error. This
is the correct degradation mode for a CPU-bound auth step: slow under burst, never failing.
The large spread (385ms fastest → 13.2s slowest) is the threadpool draining the queue one
bcrypt verify at a time. This latency profile is the baseline TC-PC-073 compares its
invalid-login burst against.

**Notes / follow-up**

The ~7.5s median (max ~13.2s) under just 40 concurrent valid logins shows bcrypt rounds=12 is
a substantial throughput limiter — relevant to the **login rate-limiting** item already
tracked in `FIX_BEFORE_PROD.md` (Auth hardening). TC-PC-073 shows the same cost is payable by
an *unauthenticated* attacker, which is the more pointed observation.
