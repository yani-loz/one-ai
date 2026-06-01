<!--
  Test-case: TC-BG-014 — platform revoke is requester-scoped (AC5, revoke side).
-->

# TC-BG-014: `POST /platform/support-requests/{id}/revoke` is requester-scoped (404 for another's)

| Field | Value |
|---|---|
| **ID** | TC-BG-014 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | ISO — cross-tenant isolation + requester-scoping |
| **Type** | Negative / Adversarial (requester scoping) |
| **Severity if it fails** | High (one admin revokes another admin's grant) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Only the platform admin who REQUESTED a grant may revoke it. The demo platform admin
attempting to revoke a grant requested by a DIFFERENT platform identity must get `404`. The
positive control (the actual requester revokes it → `200`, `revoked`) proves the demo admin's
404 was specifically "not the requester," not "non-existent" or "unrevokable." Proves
PC-05-AC5 (revoke side).

## Break hypothesis
If `revoke` loaded the grant by `id` alone (via a non-requester-scoped lookup), any platform
admin could revoke any other admin's break-glass grant — cutting off a colleague's active
incident access (an integrity/availability attack on the support flow). A bare
demo-admin-revoke → 404 is not enough: it could be 404 for any reason. Ordering the revokes
(demo fails 404, then the real requester succeeds 200) is what makes the 404 mean exactly
"requester mismatch."

## Preconditions
- Live stack `:8000`. Demo platform admin logged in.
- One fresh run-stamped org A (prefix `iso-014-a`).
- A SECOND platform token forged via `forge_platform_token()` (random `sub`) — the real
  requester of the grant under test (documented dev-secret capability).

## Steps
1. Forge platform token `F` (random `sub`).
2. As `F`, request break-glass access to A → grant `G` (`requested`,
   `requested_by_admin_id == F.sub`).
3. As the DEMO platform admin, POST `/platform/support-requests/{G}/revoke` → expect `404`.
4. Positive control — as `F` (the real requester), POST the same revoke → `200`,
   `status='revoked'`. Because a revoke is only legal from `requested|approved`, the
   200-from-`requested` proves `G` was still `requested` after the demo admin's 404.
5. psql ground-truth (audit trail): the only `actor_id` on `G` (request + revoke) is `F` —
   the demo admin's 404'd attempt left ZERO trace, the airtight "touched nothing" proof.

## Expected result
- Demo-admin revoke of F's grant: `404`.
- F's own revoke: `200`, `status='revoked'`, `is_active=false`; its success from `requested`
  proves the demo admin left the grant untouched.
- psql audit: `support.requested` + `support.revoked` on `G` both carry `actor_id == F`; no
  demo-admin actor row exists for `G`.

## Harness
Script: `harness/tc_014.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_014.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 18:20 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The demo platform admin's revoke of a grant requested by the forged admin returned 404; the
> grant stayed `requested` (psql). The actual requester then revoked it successfully (200 →
> revoked), proving the demo admin's 404 was a requester-scope block, not non-existence.

**Evidence**

```
forged sub=7fde1bb1-4fc1-4ec3-b4bc-8c3f53a72ee8
request as F -> status=201 grant_id=9806411a-94ab-4d3b-b109-bf6b89542e7a status=requested requested_by_admin_id=7fde1bb1...
demo-admin revoke F's grant -> status=404 body={'detail': 'Support grant not found.'}
F revoke own grant (positive control) -> status=200 status=revoked is_active=False
ASSERT demo_revoke==404 AND F_revoke==200/revoked -> PASS

# psql ground-truth — the audit trail proves the demo admin's 404 was a pure no-op:
#   action=support.requested  actor_id=7fde1bb1...(F)
#   action=support.revoked    actor_id=7fde1bb1...(F)
# Only F appears as an actor on G; the demo admin's revoke attempt left ZERO trace.
# (Strongest "untouched" evidence: the successful own-revoke from `requested` also proves
#  the grant was still `requested` after the demo admin's attempt.)
```

**Verdict**

Defense held. The platform revoke is requester-scoped: the demo admin cannot revoke a grant
it did not request (404, nothing touched), while the true requester can (200 → revoked). The
ordered positive control proves the 404 means "not the requester," not "non-existent."
Confirms PC-05-AC5 (`test_platform_cannot_revoke_another_admins_grant`). Code path:
`routes/support_routes.py:72-81` → `services/platform_support_service.py:96-98`
(`get_for_requester` None → `SupportGrantNotFoundError`) →
`repositories/support_grant_repository.py:43-59`
(`WHERE id = :id AND requested_by_admin_id = :me FOR UPDATE`) → `error_handlers.py:51`.

**Notes / follow-up**

Using a forged second platform token to be the "other" requester is the documented dev-secret
capability (Rotate JWT_SECRET); the requester-scoping control under test is fixed and holds →
CONFIRMS-FIXED. Companion to TC-BG-013 (list side).
