# TC-ER-021: Compliance export = metadata + trail, content-blind (AC7)

| Field | Value |
|---|---|
| **ID** | TC-ER-021 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | RETAIN — append-only audit retained + compliance export |
| **Type** | Positive |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove PC-06-AC7: `GET /platform/orgs/{id}/compliance-export` returns 200 with the
`organization` 7-field detail (incl `legal_hold`), an `audit` list, and `generated_at`. The
bundle is content-blind: no tenant content, no password/hash/token VALUE anywhere in the body.

## Break hypothesis
The attacker's bet: the export leaks a secret — a `password_hash`, a raw/hashed refresh-token
value, or a JWT — embedded in the serialized bundle (e.g. via an audit `details` field or an
over-broad org serializer). NEW finding = any such secret VALUE is present. (A retained
`actor_email`/`ip_address` inside an audit entry is documented table-scope retention, NOT a
leak.)

## Preconditions
- Live stack `:8000`; harness inside the backend container over stdin.
- Fresh run-stamped org `X` (slug prefix `retain-er021`) with history (onboard + login + a
  support request/approve). Not erased in this case.
- Platform auth via canonical `/platform/login`.

## Steps
1. Platform login; `provision_company` → org `X`.
2. `request_support(X)` → `company_approve(X, grant_id)`.
3. `compliance_export(X)` → 200.
4. Assert `organization` carries exactly id/name/slug/status/user_count/legal_hold/created_at;
   `audit` is a non-empty list; `generated_at` present.
5. Serialize the ENTIRE body and scan for forbidden secret substrings.

## Expected result
- 200; `organization` has the 7 detail fields incl `legal_hold`; `audit` a list of
  metadata-only entries; `generated_at` present. No forbidden secret VALUE in the body.

## Harness
Script: `harness/tc_021.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_021.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:43 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Export returned 200. `organization` carried exactly the 7 fields
> {created_at, id, legal_hold, name, slug, status, user_count} with `legal_hold:false`. `audit`
> was a list of 4 metadata-only entries (support.approved, support.requested,
> auth.login.success, org.onboard). `generated_at` present. The secret-substring scan over the
> full serialized body found ZERO forbidden tokens. The only PII present was `actor_email` (the
> tenant admin) inside audit entries — the documented table-scope retention.

**Evidence**

```
=== TC-ER-021 ===
ORG_ID: ab784237-efb9-4996-9347-cc828498ff73
SLUG: retain-er021-19e847f8aff1da6
SETUP request/approve: 201 200
EXPORT: 200
ORG_FIELDS: ['created_at', 'id', 'legal_hold', 'name', 'slug', 'status', 'user_count']
ORG_FIELDS_MATCH: True
LEGAL_HOLD PRESENT: True value: False
AUDIT IS LIST: True len: 4
AUDIT ACTIONS: ['support.approved', 'support.requested', 'auth.login.success', 'org.onboard']
GENERATED_AT PRESENT: True value: 2026-06-01T18:43:33.652834Z
SECRET_SUBSTRING_HITS: []
RETAINED actor_email (documented, expected): admin-retain-er021-19e847f8aff1da6@oneai.dev
RESULT: PASS
```

**Verdict**

The defense held. The export is metadata + the audit trail only — `export_compliance`
(`erasure_service.py:140-162`) builds the 7-field `OrganizationDetailResponse` from
`get_with_user_count` and the metadata-only `AuditLogEntryResponse` list; neither surfaces a
password/hash/token (the audit writer guarantees `details` carries no secret —
`audit_service.py` Key invariants). Content-blind contract upheld; confirms PC-06-AC7. The
`actor_email` retention is documented (PR-6 review dismissed; `FIX_BEFORE_PROD`).

**Notes / follow-up**

A signed/streamed export and the 1000-entry cap are documented/tracked (`FIX_BEFORE_PROD`).
Related: TC-ER-022 (export survives after erase).
