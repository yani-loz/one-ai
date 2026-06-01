<!--
  Test-case: TC-BG-030. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-030: Audience confinement — company token on /platform/* and platform token on /support-access/* both 401

| Field | Value |
|---|---|
| **ID** | TC-BG-030 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Negative / Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
PC-05-AC6: the two break-glass domains are physically separated by the JWT `aud` claim. A
*real, legitimately-issued* company_admin token must be rejected (401) on the platform request
endpoint, and a *real* platform-admin token must be rejected (401) on the company approve
endpoint. Neither side accepts the other's credential.

## Break hypothesis
A token validated only for signature + expiry (not audience) would let a company_admin POST a
break-glass request (privilege escalation into the platform domain) or let a platform admin
self-approve a grant on the company endpoint (bypassing the structural consent gate). The bet:
one direction is checked but the mirror is not, so one of the two requests returns 201/200.

## Preconditions
- Live stack `:8000`. Demo platform admin logs in (real platform token).
- One fresh run-stamped org `aea30-<stamp>` provisioned via `provision_company`; its admin's
  real company token is the cross-domain probe. A real `requested` grant on that org is created
  so the platform→company approve attempt hits a genuine, approvable grant (not a 404 short
  circuit) — the audience gate must fire BEFORE the grant is loaded.

## Steps
1. Platform login → real platform token; provision org A → real company_admin token.
2. Direction 1: with the **real company token**, `POST /platform/orgs/{A}/support-requests`.
3. Create a real grant on A (platform requests it) so an approvable target exists.
4. Direction 2: with the **real platform token**, `POST /support-access/{grant}/approve`.

## Expected result
- Direction 1 (company token → platform request): **401** (`get_current_platform_admin` rejects
  `aud='company'`).
- Direction 2 (platform token → company approve): **401** (`get_current_principal` /
  `require_company_admin` rejects `aud='platform'`), and the grant stays `requested`.

## Harness
Script: `harness/tc_030.py` · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_030.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 21:40 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> Both directions were rejected with 401. The real company_admin token could not POST a
> platform support-request; the real platform token could not approve on the company endpoint.
> The targeted grant remained `requested` after the rejected platform-token approve (confirmed by
> the platform requester's own list) — the audience gate fired before any state change.

**Evidence**

```
1) provisioned A=15f8ec15-6a1e-4632-9a73-b56f6766e09b (company_admin token issued)
2) company_token -> POST /platform/.../support-requests : 401 body={"detail":"Access token is invalid."}
3) platform requested grant on A: 201 status=requested grant=97b9d1b6-497a-4a4c-b128-c0e5a92c1e2d
4) platform_token -> POST /support-access/{gid}/approve : 401 body={"detail":"Access token is invalid."}
5) grant still requested after rejected approve? status=requested is_active=False -> True
PASS both directions 401; grant untouched
```

**Verdict**
The defense held in **both** directions. Audience confinement is enforced at the dependency
layer: `get_current_platform_admin` (dependencies.py:110-124, `decode_access_token(..., PLATFORM_AUDIENCE)`)
rejects the company token, and the company router's `require_company_admin` →
`get_current_principal` (dependencies.py:79-107, `decode_access_token(..., COMPANY_AUDIENCE)`)
rejects the platform token. The 401 fires before the service loads the grant, so no state change
leaks. Confirms PC-05-AC6 and the FIX_BEFORE_PROD "keep platform-admin auth physically separate"
invariant under live conditions.

**Notes / follow-up**
The 401 body is the generic "Access token is invalid." — no audience-specific detail is leaked,
which is correct (no oracle on which audience was expected). Mirror of the forged-token
self-approve capability (TC-BG-consent suite, documented Rotate-JWT_SECRET): the gate rests on a
*correctly-audienced* token; a forged company token still passes here because the dev secret is
shared — that is the tracked deferral, not this case (this case uses only real tokens).
