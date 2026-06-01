<!--
  Test-case: TC-BG-010 — inbox cross-tenant isolation (AC3, inbox side).
-->

# TC-BG-010: Support inbox is org-scoped — A's grant visible to A, absent from B

| Field | Value |
|---|---|
| **ID** | TC-BG-010 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | ISO — cross-tenant isolation + requester-scoping |
| **Type** | Negative / Adversarial (cross-tenant) |
| **Severity if it fails** | High (cross-tenant existence/metadata leak) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A break-glass grant targeting org A must appear in A's approval inbox (`GET /support-access`
as A-admin) and must be ABSENT from B's inbox. Proves PC-05-AC3 on the read (inbox) side —
the company HITL inbox is scoped by `org_id` so one company never sees another's pending
support requests.

## Break hypothesis
If `list_for_org` dropped (or weakened) its `WHERE org_id = :caller_org` filter, B-admin
would see A's grant in their inbox — a cross-tenant metadata + existence leak (who is asking
for break-glass access into A, the reason, the requester email). The attacker's bet: the
inbox query is unscoped or scoped by something other than the verified JWT org_id.

## Preconditions
- Live stack `:8000`; demo platform admin onboards two FRESH run-stamped orgs A + B, each
  with its own company_admin (via `provision_company`, prefixes `iso-010-a` / `iso-010-b`).
- Demo platform admin is only used to onboard + request — never mutated.

## Steps
1. Platform admin requests break-glass access to org A → expect `201`, status `requested`.
2. List A-admin's inbox (`GET /support-access`) → the grant id MUST be present.
3. List B-admin's inbox → the grant id MUST be absent.
4. (Defense-in-depth) assert A's inbox row carries the expected `org_id == A`.

## Expected result
- Request: `201`, body `status='requested'`, `org_id == A`.
- A inbox: `200`, contains a row whose `id` == the grant id, `org_id == A`.
- B inbox: `200`, does NOT contain the grant id (asserted by id, not by count — the DB is
  shared/persistent so count is unreliable).

## Harness
Script: `harness/tc_010.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_010.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 18:12 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The grant targeting A appears in A-admin's inbox (matched by id, org_id=A) and is absent
> from B-admin's inbox. Both inboxes return 200. The org filter on `list_for_org` holds.

**Evidence**

```
orgA=70b2... slug=iso-010-a-...   orgB=2a1c... slug=iso-010-b-...
request status=201 grant_id=<G> status=requested org_id=A
A inbox status=200  grant_in_A_inbox=True  A_inbox_row_org_id_matches_A=True
B inbox status=200  grant_in_B_inbox=False
ASSERT present_in_A=True absent_from_B=True -> PASS
```

**Verdict**

Defense held. The inbox read is org-scoped by the verified JWT `org_id` — A's grant is
visible only to A, invisible to B. Confirms PC-05-AC3 (inbox side) and the fix backing
`test_company_inbox_is_org_scoped`. Code path: `routes/support_routes.py:87-93`
(`list_org_support_requests` passes `principal.org_id` from the verified token) →
`services/company_support_service.py:65-68` (`list_for_org`) →
`repositories/support_grant_repository.py:83-90` (`SELECT … WHERE org_id = :org_id`).

**Notes / follow-up**

Asserted by grant_id (not count) per the shared/persistent-DB constraint. Companion to
TC-BG-011/012 (the cross-tenant decide path).
