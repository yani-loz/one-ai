| ID · Suite · Type · Mode | TC-IM-A03 · A (Connection plane & credential cipher) · Negative · http |
|---|---|
| Result ✅Pass · Tag — · Severity — (defense held) · Status Executed | |

## Objective
Prove `POST /connectors` rejects an extra `org_id` field in the body (`extra='forbid'`) and a `connector_type` outside the enum — both with **422**, never a silent honor.

## Break hypothesis
If `CreateConnectionRequest` allowed extra fields, a caller could smuggle `org_id` into the body and (if the route ever trusted it) write to another org. The org must come ONLY from the verified JWT (`principal.org_id`); the body must not be able to set it.

## Steps
1. `POST /connectors` with a valid body **plus** `"org_id": <random uuid>` → expect 422.
2. `POST /connectors` with `"connector_type": "smtp"` (outside the `ConnectorType` enum) → expect 422.

## Expected
Both 422; the smuggled `org_id` never reaches the service (and even if it did, the route ignores the body org and uses `principal.org_id`).

## Execution result (2026-06-09)
```
[PASS] a03_smuggled_org_id_rejected_422 :: status=422
[PASS] a03_bad_connector_type_rejected_422 :: status=422
```
**Verdict:** ✅ Pass. `connector_schemas.py:28` (`model_config = ConfigDict(extra="forbid")`) rejects the smuggled column; the enum-typed `connector_type` rejects `smtp`. **Tag:** — (positive/contract).
