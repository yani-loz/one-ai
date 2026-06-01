<!--
  Test-case: TC-BG-011 — cross-tenant approve → 404, grant untouched (AC3, decide side).
-->

# TC-BG-011: B-admin approving A's grant → 404, grant left untouched

| Field | Value |
|---|---|
| **ID** | TC-BG-011 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | ISO — cross-tenant isolation + requester-scoping |
| **Type** | Negative / Adversarial (cross-tenant) |
| **Severity if it fails** | High (cross-tenant approval = consent forgery) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A company_admin of org B must NOT be able to approve a break-glass grant targeting org A.
The attempt must resolve to `404` (not `403`, not `200`-empty) with NO existence leak, and
the grant must remain UNTOUCHED (`status='requested'`, no decider stamped). Proves the
consent gate cannot be operated cross-tenant (PC-05-AC3, decide side).

## Break hypothesis
If the company approve path loaded the grant by `id` alone (without the `org_id` filter) or
returned `403`/`200`-empty, B-admin could either approve A's grant (manufacturing consent for
a tenant they don't control) or learn the grant exists (existence oracle). The attacker's
bet: the org filter is missing or the not-found maps to something other than 404.

## Preconditions
- Two fresh run-stamped orgs A + B (prefixes `iso-011-a` / `iso-011-b`), each its own admin.
- One `requested` grant targeting A (platform admin requests it).

## Steps
1. Platform admin requests break-glass access to A → `201`, grant id `G`, status `requested`.
2. B-admin POSTs `/support-access/{G}/approve` → expect `404`.
3. Existence-oracle check: B-admin POSTs approve on a RANDOM nonexistent `grant_id` →
   expect `404` with the IDENTICAL body — proving the cross-org case is
   byte-indistinguishable from truly-absent (no existence oracle).
4. Read back via A-admin's inbox: find `G`, assert `status=='requested'`, `decided_at` null,
   `decided_by_email` null (untouched).
5. psql ground-truth: `SELECT status, decided_at, decided_by_email FROM support_grant
   WHERE id = G` → `requested / NULL / NULL`.

## Expected result
- Cross approve: `404`, body is the not-found detail (no A-grant fields echoed).
- Nonexistent approve: `404`, body IDENTICAL to the cross-org body (oracle-safe).
- Read-back (A inbox + psql): `status='requested'`, `decided_at IS NULL`,
  `decided_by_email IS NULL` — the grant is byte-for-byte unchanged.

## Harness
Script: `harness/tc_011.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_011.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 18:14 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> B-admin's cross-tenant approve returned 404 (SupportGrantNotFoundError), not 403 or
> 200-empty — and with a body BYTE-IDENTICAL to a truly-nonexistent grant_id (no existence
> oracle). The grant stayed `requested` with no decider stamped, confirmed via A's inbox
> read-back AND direct psql.

**Evidence**

```
orgA slug=iso-011-a-...  orgB slug=iso-011-b-...
request status=201 grant_id=fffa1742-e1b0-43c0-81a1-def6beff239e status=requested
B-admin approve A's grant       -> status=404 body={'detail': 'Support grant not found.'}
B-admin approve NONEXISTENT id  -> status=404 body={'detail': 'Support grant not found.'}
existence_oracle_safe (cross-org == truly-absent) = True
A inbox read-back: status=requested decided_at=None decided_by_email=None
ASSERT cross_approve==404 AND untouched AND oracle_safe -> PASS

# psql ground-truth from the first run (db container):
#   SELECT status, decided_at, decided_by_email FROM support_grant WHERE id='a126e71d-...';
#    status   | decided_at | decided_by_email
#   requested |   (null)   |     (null)
```

**Verdict**

Defense held. The company approve path filters by the caller's verified `org_id` BEFORE any
state logic, so a cross-org grant resolves to None → 404 with no existence leak, and the
grant is untouched. Confirms PC-05-AC3 (`test_cross_tenant_approve_returns_404`). Code path:
`routes/support_routes.py:96-103` → `services/company_support_service.py:79`
(`_load_requested` → `:131-133` raises `SupportGrantNotFoundError` when `get_in_org` is None)
→ `repositories/support_grant_repository.py:61-72` (`WHERE id = :id AND org_id = :org_id`) →
`error_handlers.py:51` (`SupportGrantNotFoundError → 404`).

**Notes / follow-up**

No single-grant company GET exists, so "untouched" is proven via the org-scoped inbox plus
direct psql. Companion to TC-BG-012 (deny + revoke). The forged-`company_admin`-self-approve
risk (dev secret) is a separate, documented capability — see the CONSENT suite.
