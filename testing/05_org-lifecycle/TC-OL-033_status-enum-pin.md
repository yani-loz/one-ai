# TC-OL-033: PATCH status enum pin (invalid/case/empty/extra/missing → 422)

| Field | Value |
|---|---|
| **ID** | TC-OL-033 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Negative / Boundary |
| **Severity if it fails** | Medium (an unpinned status could set an invalid lifecycle state, bypassing the suspend gate semantics) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`PATCH /platform/orgs/{id}/status` pins `status` to the lowercase `OrganizationStatus` enum:
invalid value, wrong case, empty string → 422; an extra field → 422 (extra=forbid); a
missing `status` → 422 (PC-03a-AC1).

## Break hypothesis
Status validation is case-insensitive or accepts an out-of-enum value (e.g. `deleted`), or
`extra="forbid"` is missing so unexpected fields are silently accepted — letting an org be
moved to an undefined lifecycle state.

## Preconditions
Live stack. Fresh run-stamped org (`contract33-<stamp>`). Demo platform token. None of the
rejected PATCHes mutate state (all fail validation), so the org stays `active`.

## Steps
1. Platform-login; `provision_company(prefix="contract33")`.
2. PATCH status with `deleted`, `ACTIVE`, `Suspended`, `''`.
3. PATCH with `{status:"active", extra:"x"}` (extra field).
4. PATCH with `{}` (missing status).
5. Assert 422 for every variant.

## Expected result
422 for each (enum error for value/case/empty, `extra_forbidden`, `missing`).

## Harness
Script: `harness/tc_033.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_033.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Every invalid value, uppercase/titlecase variant, and the empty string returned 422 with
> an `enum` error pinning the exact lowercase set. The extra field returned 422
> `extra_forbidden`; missing status returned 422 `missing`.

**Evidence**

```
status='deleted' -> 422 {"type":"enum",...,"msg":"Input should be 'active', 'suspended', 'onboarding' or 'offboarded'","input":"deleted"}
status='ACTIVE' -> 422 {"type":"enum",...,"input":"ACTIVE"}
status='Suspended' -> 422 {"type":"enum",...,"input":"Suspended"}
status='' -> 422 {"type":"enum",...,"input":""}
extra_field -> 422 {"type":"extra_forbidden","loc":["body","extra"],"msg":"Extra inputs are not permitted","input":"x"}
missing_status -> 422 {"type":"missing","loc":["body","status"],"msg":"Field required","input":{}}
```

**Verdict**

The defense held. `OrganizationStatusUpdateRequest`
(`backend/app/identity/schemas/platform_schemas.py:86-91`) types `status` as the
`OrganizationStatus` StrEnum (lowercase, case-sensitive) with `extra="forbid"`. Confirms
PC-03a-AC1.

**Notes / follow-up**

Org left `active` (all PATCHes were rejected pre-mutation). Verified by psql afterward.
