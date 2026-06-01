# TC-OL-032: Malformed (non-UUID) path → 422, not 500

| Field | Value |
|---|---|
| **ID** | TC-OL-032 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative / Boundary |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`GET /platform/orgs/{org_id}` with a non-UUID path segment is rejected by FastAPI's `UUID`
path validation with 422 — never a 500 from an unparsed value reaching the service/DB.

## Break hypothesis
A malformed path id slips past validation and reaches the repository/DB cast, producing a
500 (information disclosure / unhandled error) instead of a clean 422.

## Preconditions
Live stack. A VALID platform token is supplied so the only variable under test is the path
shape (isolates from a missing-auth 401). Several malformed segments probed.

## Steps
1. Platform-login (valid token).
2. `GET /platform/orgs/<bad>` for `not-a-uuid`, `12345`, `deadbeef`, `abc-def-ghi`.
3. Assert each is 422.

## Expected result
422 (`uuid_parsing`) for each; never 500.

## Harness
Script: `harness/tc_032.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_032.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Every malformed path segment returned 422 with a `uuid_parsing` error from FastAPI path
> validation — the value never reached the service. No 500.

**Evidence**

```
GET /platform/orgs/not-a-uuid -> status=422 body={"detail":[{"type":"uuid_parsing","loc":["path","org_id"],"msg":"Input should be a valid UUID, invalid character: found `n` at 1","input":"not-a-uuid",...}]}
GET /platform/orgs/12345 -> status=422 body={"detail":[{"type":"uuid_parsing","loc":["path","org_id"],"msg":"Input should be a valid UUID, invalid length: expected length 32 for simple format, found 5","input":"12345",...}]}
GET /platform/orgs/deadbeef -> status=422 body={"detail":[{"type":"uuid_parsing",...,"input":"deadbeef",...}]}
GET /platform/orgs/abc-def-ghi -> status=422 body={"detail":[{"type":"uuid_parsing",...,"input":"abc-def-ghi",...}]}
```

**Verdict**

The defense held. The `org_id: UUID` path param in
`routes/platform_routes.py:134` makes FastAPI reject non-UUID segments at the framework
boundary with 422 before any service code runs. No 500. Confirms robust input validation.

**Notes / follow-up**

Note: path validation here ran even with a valid token present — observed ordering returned
422 for the path, not 401, but this case deliberately supplied a valid token so the result
is unambiguous.
