<!--
  Test-case: TC-BG-034. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-034: Input bounds — empty reason, over-length reason, and extra field all rejected (422)

| Field | Value |
|---|---|
| **ID** | TC-BG-034 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Boundary / Negative |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
`SupportAccessRequest` bounds the only caller-supplied free text: `reason` is `min_length=1`,
`max_length=500`, and the model is `extra='forbid'`. An empty reason, an over-length reason
(>500), and any extra body field must each be rejected at validation (422) before a grant is
created.

## Break hypothesis
If the bounds were absent or `extra` were the Pydantic default (`ignore`), an empty/oversized
reason would persist (a junk consent record with no real justification) or an extra field would be
silently dropped (mass-assignment surface — e.g. a caller smuggling `status: approved`,
`org_id`, or `expires_at`). The bet: one of the three (empty / >500 / extra) returns 201 and
creates a grant.

## Preconditions
- Live stack `:8000`. Real platform token (demo platform admin login).
- Fresh run-stamped org `aea34-<stamp>` (provision_company) as the request target — so a
  successful (defect) creation would land on our own org, never demo/globex.
- Three malformed `POST /platform/orgs/{A}/support-requests` bodies.

## Steps
1. Body A: `{"reason": ""}` (empty → violates min_length=1).
2. Body B: `{"reason": "x"*501}` (501 chars → violates max_length=500).
3. Body C: `{"reason": "valid", "status": "approved"}` (extra field → violates extra='forbid').
   (also probes the mass-assignment angle: smuggling a privileged field.)
4. For each, assert 422 and assert no grant was created (the platform admin's request list does
   not grow with a grant on org A from these calls).

## Expected result
All three return **422** (FastAPI/Pydantic validation error). No grant row is created for any of
them. The extra-field rejection specifically blocks mass assignment of `status`/`expires_at`.

## Harness
Script: `harness/tc_034.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_034.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 22:00 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> All three malformed requests were rejected with 422. The empty reason failed `min_length`, the
> 501-char reason failed `max_length`, and the extra `status` field failed `extra='forbid'`. No
> grant on org A was created by any of them (the platform admin's request list contained zero
> grants for org A afterward).

**Evidence**

```
1) provisioned A=db006e5c-7588-46da-a43e-d1a94f2de259
2) reason="" (empty):            422  type=string_too_short  loc=['body', 'reason']
3) reason=<501 chars>:           422  type=string_too_long   loc=['body', 'reason']
4) {reason:valid, status:approved} (extra): 422 type=extra_forbidden loc=['body', 'status']
5) grants on org A created by the above: 0  (list_my_requests filtered to org A == [])
PASS all three 422; no grant created; mass-assignment of status blocked
```

**Verdict**
The defense held. `SupportAccessRequest` (support_schemas.py:21-26) declares
`reason: str = Field(min_length=1, max_length=500)` and `model_config = ConfigDict(extra="forbid")`.
FastAPI rejects all three at the request-validation boundary (422), before the route body runs, so
no grant is created and the smuggled `status: approved` is rejected rather than silently ignored —
closing the mass-assignment surface. Confirms the input-bounds finding from the PR-5 review
(finding #5, reason 422) live and extends it to the extra-field and over-length cases.

**Notes / follow-up**
`SupportGrantResponse` is metadata-only and the service sets `status`/`expires_at` server-side, so
even if `extra` were lax the field would not be honoured — but `extra='forbid'` makes that explicit
and fails loudly, which is the stronger posture. Pairs with TC-BG-035 (injection stored literally).
