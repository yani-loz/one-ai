# TC-IA-001: `/health` reports DB reachable

| Field | Value |
|---|---|
| **ID** | TC-IA-001 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Positive |
| **Severity if it fails** | Info |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Confirm the liveness/readiness probe performs a real DB round-trip and reports it:
`GET /health` → 200 with body `database == "reachable"` (and `status == "ok"`).

## Break hypothesis
The probe claims health without actually touching Postgres (a stubbed/cached 200), or
returns a non-200, or omits/mis-sets the `database` field — so a green healthcheck would
not actually prove DB reachability. The route runs `SELECT 1` (`health.py:23`), so a
genuine break would surface as a 500 if the DB were down, or a 200 with a wrong/missing
`database` field.

## Preconditions
Live stack (`docker compose ps` → backend + db healthy). No data setup; read-only probe.
Namespace: `infra-<stamp>` (declared for the run; this case provisions no data).

## Steps
1. `GET http://localhost:8000/health`.
2. Assert status == 200, body `status == "ok"`, body `database == "reachable"`.

## Expected result
`200 OK`, JSON `{"status":"ok","service":"One AI","version":"0.1.0","database":"reachable"}`.

## Harness
Script: `harness/tc_001.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_001.py`

---

## Execution result

- **Run at:** 2026-05-31 11:48 local
- **Result:** ✅ Pass
- **Finding tag:** — (pure positive contract test)

**Actual behavior**

> `GET /health` returned 200 with `database == "reachable"` after the real `SELECT 1`.
> The backend container also reports `Up (healthy)`, consistent with the probe.

**Evidence**

```
=== GET /health ===
status: 200
content-type: application/json
body: {"status":"ok","service":"One AI","version":"0.1.0","database":"reachable"}
--- assertions ---
status==200: True
database=='reachable': True
status field=='ok': True
```

**Verdict**

Defense held. The probe behaves per contract — a 200 reflects an actual DB round-trip
(`api/routes/health.py:23` executes `SELECT 1` before returning). No concern.

**Notes / follow-up**

Baseline sanity check for the suite; confirms the stack is live before the adversarial
cases run. Related: AUD-15 (prod images bake no `HEALTHCHECK`) is an ops deferral, out of
scope for this functional probe.
