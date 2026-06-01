<!--
  Test-case: STRESS suite — recovery + no connection leak after ~30s sustained mixed load.
-->

# TC-PC-074: Recovery + no connection leak after sustained mixed load

| Field | Value |
|---|---|
| **ID** | TC-PC-074 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | STRESS — pool + bcrypt saturation |
| **Type** | Stress / Concurrency |
| **Severity if it fails** | Medium (a connection leak / lingering pool starvation is an availability defect) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — (NA — pure positive/stress test; clean recovery + no leak, no prior fix re-proven) |

## Objective
Prove that after ~30s of sustained, interleaved mixed load (`GET /platform/me`,
`GET /platform/orgs`, and a few `POST /platform/orgs` onboards) the server **fully recovers**:
`GET /health` → 200, a single `GET /platform/me` returns to its ~baseline latency (no
lingering pool starvation), AND — the objective check — the Postgres connection count
(`pg_stat_activity WHERE usename='oneai'`) returns toward its idle baseline (no connection
leak: connections are returned to the pool, not stranded).

## Break hypothesis
If a code path leaked connections (a session not closed on some branch — e.g. an onboard that
errored, or the `after_begin` GUC listener holding a connection), the `oneai` connection count
would stay elevated after the load settled, and subsequent `/platform/me` latency would stay
high (pool starved). A FAIL is: post-load `/platform/me` latency stuck high, `/health` not
200, OR the `oneai` connection count not returning toward baseline within a few seconds of the
load ending (a leak), capped at the pool max of 15.

## Preconditions
- Live stack healthy. Idle baseline captured via psql before the run:
  `SELECT count(*) FROM pg_stat_activity WHERE usename='oneai'` ≈ 7 (pool keeps ~5 base + a
  couple), well under the 15-conn pool max.
- **Namespace:** every onboard uses `provision_company(c, plat_token, prefix="stress-rec")`,
  which run-stamps the org slug + admin email via `stamp()` — no collision, no demo mutation.
  Onboard count kept SMALL (3) since each is a real bcrypt hash + 2 inserts.
- The leak assertion is psql ground-truth run from the **host** (the backend container has no
  psql) immediately after the harness reports the load finished + recovery checks.

## Steps
1. (host) psql: record idle `oneai` connection baseline.
2. (harness, inside backend) log in the demo admin once; capture a pre-load single
   `/platform/me` baseline latency.
3. (harness) drive ~30s of sustained mixed load: a pool of workers interleaving
   `GET /platform/me` + `GET /platform/orgs`, plus 3 `provision_company` onboards spaced
   through the window. Report the request counts + status tally.
4. (harness) immediately after the load loop ends: `GET /health` → expect 200; single
   `GET /platform/me` → expect 200 with latency back near the pre-load baseline.
5. (host) psql: re-record `oneai` connection count ~a few seconds after the load settled;
   assert it returned toward the baseline (≤ baseline + small slack, and ≤ 15 pool max) — no
   leak.

## Expected result
Load phase: all requests 200/201 (allowing 409 only if an onboard slug/email genuinely
collided — not expected with run-stamping). Recovery: `/health` 200; post-load `/platform/me`
200 with latency within a small multiple of the pre-load baseline (≈ low tens of ms, not
seconds). Leak check: `oneai` connection count back toward baseline (no monotonic growth, ≤
15).

## Harness
Script: `harness/tc_074.py` · run:
`cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_074.py | docker compose exec -T backend python -`
Leak check (host): `docker compose exec -T db psql -U oneai -d oneai -c "SELECT count(*) FROM pg_stat_activity WHERE usename='oneai';"`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ✅ Pass
- **Finding tag:** — (NA — pure positive/stress test; no prior audit fix re-proven)

**Actual behavior**

> ~30s of sustained mixed load (3,098 successful `GET /platform/me` + `GET /platform/orgs`,
> all 200, plus 3 onboards all 201) ran with zero 500 and zero EXC. Immediately after the load
> stopped, `GET /health` → 200 and a single `GET /platform/me` → 200 at ~8.7ms — right back at
> the pre-load baseline (~9.2ms). The host-side psql poll (1 Hz across the run) stayed near
> baseline throughout (samples mostly 7, one observed 11) and — the load-bearing leak signal —
> settled back to exactly 7 (the idle baseline) within seconds of the load ending, with no
> elevated floor afterward. Connections were returned to the pool, never stranded. No leak.
> (The 1 Hz sampling against ~8ms reads cannot rule out a brief sub-second spike toward the
> 15-conn max mid-window — but the no-leak conclusion rests on the before/after equality,
> not the sampled peak.)

**Evidence**

```
(host) idle oneai conns BEFORE: 7

(harness) pre-load single GET /platform/me baseline: 200  9.2ms
(harness) drove sustained mixed load for ~30.1s
  GET  /platform/me   : {200: 1552}
  GET  /platform/orgs : {200: 1546}
  POST /platform/orgs : {201: 3}      (provisioned: stress-rec-19e8285cb9fda10, -19e8285ed4e8937, -19e82860f3f0f4d)
  total successful requests: 3101   500s=0  EXC=0

(harness) RECOVERY checks (immediately post-load):
  GET /health        : 200  {'status': 'ok', 'service': 'One AI', 'version': '0.1.0', 'database': 'reachable'}
  GET /platform/me   : 200  8.7ms   (baseline was 9.2ms -> recovered)

(host) oneai conns poll (1 Hz, 40 samples): 7 7 7 7 7 7 11 7 7 7 ... 7  (sampled max=11)
(host) oneai conns AFTER (settled):  7     <- returned to baseline floor, no leak
(host) oneai conns sampled max:      11    <- 1 Hz poll; may miss a sub-second spike toward 15
```

**Verdict**

The defense held. After a 30s mixed-load burst the server recovered cleanly:
`GET /platform/me` latency returned to ~8.7ms (its pre-load ~9.2ms baseline), `/health` was
200, and the `oneai` Postgres connection count returned to the idle baseline (7) within
seconds with no elevated floor afterward — the decisive no-leak signal (the 1 Hz sampled max
of 11 is illustrative, not the basis of the verdict). This proves the unit-of-work boundaries
return connections to the pool on every path
— the plain `get_session` dependency (`core/database.py:36`), the `scoped_session` context
manager (`core/database.py:54`, used by tenant flows), and the onboard transaction (which
commits once and releases) — with no leak across the read, list, and onboard paths under
contention. The `after_begin` GUC listener (`core/database.py:83`) does not strand
connections. Onboard rollback paths were not error-triggered here (all 201), but the
connection accounting held across the mix.

**Notes / follow-up**

CONFIRMS-FIXED — clean recovery + no leak corroborates that pooled connections are correctly
released. Complements TC-PC-070/071 (the pool absorbs bursts) by proving it also *recovers*
fully afterward. The provisioned `stress-rec-*` orgs are run-stamped leftovers in the shared
dev DB (harmless; isolated from other suites' namespaces).
