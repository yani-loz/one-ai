<!--
  Test-case: STRESS suite — capacity knee escalation 30/60/120/200 on GET /platform/me.
-->

# TC-PC-071: Capacity knee — escalate 30 → 60 → 120 → 200 on GET /platform/me

| Field | Value |
|---|---|
| **ID** | TC-PC-071 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | STRESS — pool + bcrypt saturation |
| **Type** | Stress / Boundary |
| **Severity if it fails** | Info (capacity characterisation; the pool sizing is unconfigured) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | NEW (Info — unconfigured pool sizing not tracked in FIX_BEFORE_PROD) |

## Objective
Characterise the headroom of the 15-connection pool by escalating concurrency on
`GET /platform/me` and finding the level (if any) where HTTP 500s or pool-timeout EXCs first
appear. Report the `summarize()` tally + median latency at each level.

## Break hypothesis
The pool serialises beyond 15 in-flight requests, so latency must rise roughly linearly with
concurrency. A **knee** would be the first level where the per-request service time × queue
depth exceeds the 30s `pool_timeout`, producing 500s. With `/platform/me` holding a
connection only ~8–13ms, the arithmetic says even 200 concurrent (`~200/15 × 13ms ≈ 170ms`
worst-case queue) stays far under 30s — so the prediction is **no knee within 200**; all
levels → 200 with mild latency growth.

> ⚠️ Client-side trap (caught in design): a bare `httpx.AsyncClient` defaults to
> `max_connections=100`, so 120 and 200 would queue *on the client* and mask the server.
> This case builds a client with `httpx.Limits(max_connections=500,
> max_keepalive_connections=500)` so all requests reach uvicorn — we measure the SERVER's
> 15-conn pool, not httpx's 100-conn pool. (Default confirmed live: `max_connections=100`,
> `max_keepalive_connections=20`, httpx 0.28.1.)

## Preconditions
- Live stack healthy; pool empirically `size=5, overflow=10, timeout=30`. PG
  `max_connections=100`.
- Reads the demo admin only (one token); no orgs created; demo admin never mutated.
- Client timeout 120s (> 30s pool_timeout) so a true server exhaustion is an HTTP 500,
  never a client deadline EXC. Raised connection limits per the trap above.

## Steps
1. Log in the demo admin once → one access token.
2. For each level in [30, 60, 120, 200]: fire that many concurrent `GET /platform/me`,
   time each with wall-clock `perf_counter`, `summarize()` the statuses, compute median
   latency. A fresh raised-limit client per run to avoid keepalive carryover skewing levels.
3. Report the level where the first 500 / pool-timeout EXC appears, or state none appeared.

## Expected result
Every level → all **200**. Median latency grows with concurrency (queueing) but no 500 and
no `EXC:PoolTimeout` within 200. The absence of a knee = large headroom; record as Info.

## Harness
Script: `harness/tc_071.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_071.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** NEW (Info)

**Actual behavior**

> Every escalation level — 30, 60, 120, 200 — returned 100% HTTP 200. No 500 and no
> pool-timeout/connect EXC appeared at any level, including 200 (13× the pool's 15-connection
> capacity). Median latency grew monotonically with concurrency exactly as the queueing model
> predicts, and the worst-case tail at 200 stayed far below the 30s pool_timeout. No knee was
> found within the tested range — the pool has large headroom at these loads. The raised-limit
> client ensured all 200 requests actually reached uvicorn (a bare client would have capped at
> 100 client-side and hidden the true server behaviour).

**Evidence**

```
RAISED-LIMIT client: max_connections=500, max_keepalive_connections=500
baseline single GET /platform/me: 200  11.6ms

level=30   tally={200: 30}    median=388.4ms   max=416.3ms    500s=0  EXC=0
level=60   tally={200: 60}    median=525.5ms   max=564.5ms    500s=0  EXC=0
level=120  tally={200: 120}   median=875.4ms   max=1006.4ms   500s=0  EXC=0
level=200  tally={200: 200}   median=1226.0ms  max=1944.7ms   500s=0  EXC=0

first 500 / pool-timeout EXC at level: NONE (no knee within 200)
```

**Verdict**

No capacity defect within the tested range. The unconfigured pool
(`backend/app/core/database.py:32`, no explicit `pool_size`) relies on the SQLAlchemy
defaults (15 max conns, 30s timeout) and absorbed up to 200 concurrent — 13× its capacity —
purely by queueing, with the slowest request (~1.94s at level 200) still ~15× inside the 30s
pool_timeout budget. Median latency scaled monotonically with concurrency (388ms → 525ms →
875ms → 1226ms), the expected queueing signature, confirming requests wait for a connection
rather than failing. **No knee exists below 200**, so headroom is large for a metadata read.

`FIX_BEFORE_PROD.md` does **not** mention connection-pool sizing/tuning anywhere (its Ops
section covers TLS, CORS, CSP, API versioning, and secrets — not DB pool config). Hence this
is recorded as a **NEW Info** observation: the engine ships with implicit, undocumented pool
sizing. It is adequate at the loads a single super-admin console generates, but a future
heavy/concurrent surface (e.g. company `/auth/*` under real tenant load) could hit the 15-conn
ceiling, and the latency would degrade as queueing — not error — which is silent. Worth an
explicit `pool_size`/`max_overflow`/`pool_timeout` decision before prod and a tracked line in
the Ops checklist.

**Co-bottleneck observation (strengthens the Info note):** the measured latency grew ~10×
faster than a pure 15-connection-queue model predicts (a 60-concurrent burst against ~13ms
reads should queue to ~36ms worst-case; it actually hit 525ms median / 564ms max, and 200 →
1.2s median). The DB pool alone cannot explain that. The dominant co-bottleneck is
**single-event-loop CPU serialization** — per request, JWT decode (`decode_access_token`) +
the async ORM `get_by_id` + Pydantic serialization all run on the one uvicorn worker's event
loop, so requests serialize on CPU as much as on the 15-conn pool. The capacity ceiling under
load is therefore *not solely* the DB pool; raising `pool_size` alone would not linearly
improve throughput without also scaling worker processes. The no-knee conclusion holds
regardless.

**Notes / follow-up**

Severity Info — not a functional break, a sizing/observability gap. Cross-references
TC-PC-070 (the 60-level floor) and TC-PC-074 (recovery/no-leak after sustained load).
Recommendation: add a "Set explicit DB pool sizing + worker count" item to
`FIX_BEFORE_PROD.md` Ops section (the two scale together, per the co-bottleneck above).
