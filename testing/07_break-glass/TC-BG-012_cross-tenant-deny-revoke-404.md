<!--
  Test-case: TC-BG-012 — cross-tenant deny + revoke → 404 (AC3, org-precedes-state).
-->

# TC-BG-012: B-admin deny/revoke of A's grant → 404 (org filter precedes the state guard)

| Field | Value |
|---|---|
| **ID** | TC-BG-012 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | ISO — cross-tenant isolation + requester-scoping |
| **Type** | Negative / Adversarial (cross-tenant) |
| **Severity if it fails** | High (cross-tenant state change / existence leak) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A company_admin of org B must NOT be able to deny OR revoke a grant targeting org A — both
resolve to `404`. Crucially, on the revoke we use an APPROVED grant: a same-org caller WOULD
succeed at revoking it, so B getting `404` (not `200`, not `409`) proves the org filter fires
BEFORE the state guard — no existence leak via status code. Proves PC-05-AC3 across deny +
revoke (review finding #3).

## Break hypothesis
If the org filter and the state guard were checked in the wrong order (state first), B's
revoke of A's *approved* grant could return `409` (illegal-from-this-state) or even `200`
(succeed), each of which leaks that the grant exists and is approvable/approved. The
attacker's bet: a cross-org revoke of an approved grant returns anything other than a flat
404.

## Preconditions
- Two fresh run-stamped orgs A + B (prefixes `iso-012-a` / `iso-012-b`), each its own admin.
- One grant targeting A, driven through its lifecycle by the LEGITIMATE A-admin.

## Steps
1. Platform admin requests break-glass access to A → grant `G` (`requested`).
2. B-admin POSTs `/support-access/{G}/deny` (G still `requested`) → expect `404`.
3. A-admin approves `G` (legitimately) → `status='approved'`, `is_active=true`.
4. B-admin POSTs `/support-access/{G}/revoke` (G now `approved`) → expect `404`
   (NOT 409, NOT 200 — the org filter precedes the state guard).
5. Positive control: A-admin revokes `G` → `200`, status `revoked` (proves an approved grant
   IS revocable by the right org — so B's 404 was an isolation block, not unrevokability).
   Because a revoke is only legal from `requested|approved`, this 200-from-approved also
   proves `G` was still `approved` through B's attempt (B touched nothing).
6. psql ground-truth (final): `G.status='revoked'` — only the A-admin transition ran.

## Expected result
- Cross deny (G=requested): `404`.
- Cross revoke (G=approved): `404` — specifically not 409/200.
- A-admin revoke: `200`, `status='revoked'` (positive control); its success from `approved`
  is the proof B left the grant untouched.
- psql final: `G.status='revoked'`.

## Harness
Script: `harness/tc_012.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_012.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 18:16 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> B-admin's deny (on a requested grant) and revoke (on an APPROVED grant) both returned 404.
> The approved grant was untouched by B (psql still `approved`), and the legitimate A-admin
> then revoked it successfully (200) — proving the 404 was an org-isolation block, not
> unrevokability or non-existence.

**Evidence**

```
orgA slug=iso-012-a-...  orgB slug=iso-012-b-...
request -> grant_id=952fd401-edc2-4868-9030-2f6a6a7ec9ab status=requested
B-admin deny A's grant (requested) -> status=404 body={'detail': 'Support grant not found.'}
A-admin approve G -> status=200 status=approved is_active=True
B-admin revoke A's APPROVED grant -> status=404  (NOT 409, NOT 200)
A-admin revoke G (positive control) -> status=200 status=revoked is_active=False
ASSERT cross_deny==404 AND cross_revoke==404 AND own_revoke==200/revoked -> PASS

# The A-admin own-revoke succeeding FROM `approved` (200 -> revoked) is the proof the grant
# stayed `approved` through B's cross-revoke attempt — a revoke is only legal from
# requested|approved, so the 200 confirms B touched nothing.
# psql final state: SELECT status FROM support_grant WHERE id='952fd401-...'; -> revoked
```

**Verdict**

Defense held. The org filter precedes the state guard, so B's revoke of an *approved* grant
returns 404 (no status-based existence leak), and the grant is unchanged by B. The positive
control (A-admin revokes the same approved grant → 200) confirms 404 means "not your org," not
"unrevokable." Confirms PC-05-AC3 + review finding #3
(`test_cross_tenant_deny_returns_404` / `test_cross_tenant_revoke_returns_404`). Code path:
deny `services/company_support_service.py:103` (`_load_requested`→`:131-133`); revoke
`:118-120` (`get_in_org` None → `SupportGrantNotFoundError`) →
`repositories/support_grant_repository.py:61-72` (org-filtered load) → `error_handlers.py:51`.

**Notes / follow-up**

The ordering (org filter, then `_REVOCABLE` state check) is what defeats the status oracle.
Companion to TC-BG-010/011.
