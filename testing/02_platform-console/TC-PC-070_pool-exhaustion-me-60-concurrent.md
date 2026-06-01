<!--
  Test-case: STRESS suite — pool exhaustion via 60 concurrent GET /platform/me.
  Authored before running; Execution result block written back after running.
-->

# TC-PC-070: Connection-pool exhaustion — 60 concurrent GET /platform/me

| Field | Value |
|---|---|
| **ID** | TC-PC-070 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | STRESS — pool + bcrypt saturation |
| **Type** | Concurrency / Stress |
| **Severity if it fails** | Medium (capacity / back-pressure defect — 500s under modest load) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — (NA — pure positive/stress test; graceful queueing held, no prior fix re-proven) |

## Objective
Prove the SQLAlchemy async connection pool (no explicit `pool_size` → defaults
`pool_size=5 + max_overflow=10 = 15` max connections, `pool_timeout=30s`) **queues** an
oversubscribed burst rather than failing it. Each `GET /platform/me` performs a DB
`get_by_id`, so it must borrow a pooled connection; 60 concurrent requests oversubscribe the
15-connection pool 4×.

## Break hypothesis
At 60 concurrency the 15-conn pool is oversubscribed 4×. If the server has no graceful
back-pressure, excess requests either error with HTTP 500 (SQLAlchemy `TimeoutError` after
`pool_timeout=30s` bubbling up) or the harness sees a client-side `EXC:*`. **A FAIL is any
500 or pool-timeout EXC at merely 60 concurrency.** PASS is all 60 → 200 with a visible
latency spread (proving requests queued for a connection, not failed).

## Preconditions
- Live stack up (`docker compose ps` healthy). Engine pool empirically confirmed:
  `AsyncAdaptedQueuePool` size=5, max_overflow=10, timeout=30.0.
- Postgres `max_connections=100` (ample headroom — the pool, not the DB, is the limiter).
- Run-stamp namespace: STRESS suite, stamp via `stamp()`. This case only reads the demo
  admin (one access token); no orgs created, demo admin never mutated.
- Client timeout = 120s (WELL above the 30s server pool_timeout) so a true server-side
  exhaustion surfaces as an HTTP 500, never as a client deadline EXC.

## Steps
1. Log in the demo platform admin once → one valid access token.
2. Capture a single-request baseline latency for `GET /platform/me`.
3. Fire 60 concurrent `GET /platform/me` with that token, each timed with `perf_counter`
   wall-clock around the full await (captures queue wait).
4. `summarize()` the status tally; compute min/median/max latency.

## Expected result
All 60 → **200** (graceful queueing). A latency spread is expected and healthy: with 15
connections each held ~8ms, the tail requests wait roughly `(60/15)×8 ≈ 32ms` — a small,
bounded spread, NOT a 30s pool-timeout. Zero 500s, zero `EXC:*`.

## Harness
Script: `harness/tc_070.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_070.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** — (NA — pure positive/stress test; no prior audit fix re-proven)

**Actual behavior**

> All 60 concurrent `GET /platform/me` returned 200. No 500, no client EXC. A clear,
> bounded latency spread appeared (min ~229ms → max ~502ms vs a ~13ms single-request
> baseline), exactly the signature of requests queueing for one of the 15 pooled connections
> and then succeeding — graceful back-pressure, not failure. The 4× oversubscription was
> absorbed entirely by queueing, ~17ms inside the 30s pool_timeout budget.

**Evidence**

```
baseline single GET /platform/me: 200  13.5ms
fired 60 concurrent GET /platform/me (client timeout=120s)
status tally: {200: 60}
latency ms  -> min=229.4  median=338.0  max=502.4
500 count: 0   client-EXC count: 0
sample body: {'id': '609f2b17-bee9-4f7f-a26d-cb08f666497a', 'email': 'super@ethera.ai', 'full_name': 'Ethera Super Admin'}
```

**Verdict**

The defense held. The 15-connection pool (`backend/app/core/database.py:32`,
`create_async_engine(...)` with default `pool_size=5 + max_overflow=10`) absorbed a 4×
oversubscription via the `AsyncAdaptedQueuePool` 30s wait queue — every request got a
connection well inside the window and returned 200. Latency grew from a ~13ms baseline to a
~502ms tail (the visible queueing spread the hypothesis required), ~60× below the 30s
pool_timeout that would have produced a 500. No capacity/back-pressure defect at 60
concurrency.

**Notes / follow-up**

This is the floor; TC-PC-071 escalates to 200 to characterise headroom. The pool is
unconfigured (relies on SQLAlchemy defaults) — adequate here, but cross-referenced as a NEW
Info observation in TC-PC-071 since `FIX_BEFORE_PROD.md` does not mention pool tuning.
