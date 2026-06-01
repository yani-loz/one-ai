# TC-OL-030: Org detail returns metadata only (7 fields, content-blind)

| Field | Value |
|---|---|
| **ID** | TC-OL-030 |
| **Target** | Org Lifecycle (PC-03a) |
| **Suite** | Detail + status + legal-hold + authz contracts (CONTRACT) |
| **Type** | Positive / Contract |
| **Severity if it fails** | High (platform admin must never see tenant content — data sovereignty) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`GET /platform/orgs/{id}` returns EXACTLY the 7 metadata fields
`{id, name, slug, status, user_count, legal_hold, created_at}` and nothing else — no
password hash, content, cost, token usage, admin email, or user list (PC-03a-AC2; SPEC §4
"metadata only"; FIX_BEFORE_PROD "never let platform admin read tenant content").

## Break hypothesis
The detail response model leaks an extra attribute (e.g. an `admin_email`, `users`, or a
serialized ORM relationship) beyond the 7 declared fields, exposing tenant-adjacent data to
the platform domain. A violation = any forbidden key present, or the key-set != the exact 7.

## Preconditions
Live stack. Onboard a fresh run-stamped org (`contract30-<stamp>`) via `provision_company`;
read its detail with the demo platform admin token. No mutation.

## Steps
1. Platform-login the demo admin.
2. `provision_company(prefix="contract30")` → fresh org id.
3. `GET /platform/orgs/{org_id}` with the platform token.
4. Assert the response key-set == the exact 7 fields and intersects no forbidden key.

## Expected result
200; body keys == `{id, name, slug, status, user_count, legal_hold, created_at}` exactly; no
forbidden field present.

## Harness
Script: `harness/tc_030.py` · run: `docker compose exec -T backend python - < testing/05_org-lifecycle/harness/tc_030.py`

---

## Execution result

- **Run at:** 2026-06-01 13:17 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> GET returned 200 with exactly the 7 contracted metadata fields; the key-set matched the
> expected set exactly and no forbidden field (hash/content/cost/token/admin_email/users)
> appeared. The detail endpoint is content-blind.

**Evidence**

```
GET detail status=200
fields=['created_at', 'id', 'legal_hold', 'name', 'slug', 'status', 'user_count']
expected_exactly=['created_at', 'id', 'legal_hold', 'name', 'slug', 'status', 'user_count']
matches_exactly=True
leaked_forbidden_fields=[]
raw_body={'id': 'b7636730-8be3-41dc-8922-52398b377130', 'name': 'Org contract30-19e835536f02621', 'slug': 'contract30-19e835536f02621', 'status': 'active', 'user_count': 1, 'legal_hold': False, 'created_at': '2026-06-01T13:17:40.470299Z'}
```

**Verdict**

The defense held. The contract shape is pinned by `OrganizationDetailResponse`
(`backend/app/identity/schemas/platform_schemas.py:71-83`) which declares exactly these 7
fields, and `PlatformOrgService._to_detail`
(`backend/app/identity/services/platform_org_service.py:77-90`) assembles them explicitly —
it never serializes the ORM object, so no relationship/content can leak. Confirms PC-03a-AC2
and the audit's "detail endpoint is content-blind" finding live.

**Notes / follow-up**

Cross-references TC-OL-031 (unknown id → 404). Content-blindness is the platform-domain
invariant in FIX_BEFORE_PROD "Never let platform admin read tenant content — metadata only".
