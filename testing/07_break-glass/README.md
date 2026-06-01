# Target 07 — Break-glass support access (PC-05) — Adversarial Validation

> Dynamic, adversarial validation of the **PC-05 break-glass** grant lifecycle against the **live stack**:
> a platform admin **requests** time-boxed access to a tenant; a **company_admin of that tenant must
> approve** (consent); the grant is time-boxed (live expiry) and every step is logged. Companion to
> `docs/audits/2026-06-01_platform-break-glass-pr5-review.md` (6 findings fixed, 0 functional defects) and
> `docs/PM/platform-console/EPIC-PC-05-break-glass.md`. Case code **`BG`** (`TC-BG-NNN`).

## Scope

**In scope:**
- ⭐ **Consent:** no platform path can produce `approved` (the platform service has no approve method); the
  company approve endpoint is the only one. The adversarial flip: a **forged dev-secret `company_admin`
  token** can impersonate the customer and self-approve (documented dev-secret blast radius).
- ⭐ **Cross-tenant isolation:** a company_admin sees/decides **only their org's** grants (`get_in_org`);
  another org's grant → invisible inbox + approve/deny/revoke → **404** (no existence leak).
- ⭐ **State machine:** illegal transitions → **409** (approve a non-`requested`; revoke a `denied`/`revoked`).
- ⭐ **No lost updates:** concurrent transitions on one grant serialize (`SELECT … FOR UPDATE`) — a revoke
  can't be overwritten back to `approved`.
- **Live expiry:** `is_active = approved AND now < expires_at` (4h), computed not stored — a past
  `expires_at` reads `is_active=false` though `status` stays `approved` (psql-assisted).
- **Audience confinement:** company token → platform request → 401; platform token → company approve → 401.
- **Requester-scoping:** a platform admin lists/revokes only their own requests (another admin's → 404).
- **Audit emission (→ PC-04):** `support.requested/approved/denied/revoked`; `support.approved` carries
  `expires_at` in `details`.
- **Content-blindness / input:** `SupportGrantResponse` is metadata only; `reason` bounded (1..500),
  injection stored literally.

**Out of scope:** the frontend request panel + HITL inbox (PC-05b — a Playwright pass); the content-access
gate (`grant_is_active` is a forward hook — no content endpoints exist yet, so an active grant unlocks nothing).

## Environment

- Live stack `:8000`; harness inside the backend container, self-contained over stdin:
  `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/<script>.py | docker compose exec -T backend python -`
- psql ground-truth on the **db** container. Demo platform admin onboards fresh orgs; **never mutated**.
- **HARD RULE:** never act on demo/globex; provision your own run-stamped orgs (`provision_company`).

## Status dashboard

> Result: ✅ pass · ❌ fail (a defect/the win) · ⚠️ pass-with-concern. Tag: 🆕 NEW · ✔ CONFIRMS-FIXED ·
> ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a. Filled during synthesis.

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| CONSENT — approval path + forged-token | _pending_ | | | no platform approve; forged company_admin self-approves (documented) |
| ISO — cross-tenant + requester-scope | TC-BG-010..014 | 5 ✅ | 0 | inbox org-scoped; cross approve/deny/revoke → 404 (no existence leak); platform list+revoke requester-scoped (positive controls) |
| STATE — transition machine + row-lock | _pending_ | | | 409 matrix; concurrent approve/revoke serialize |
| AUDIENCE+EXPIRY+AUDIT | _pending_ | | | 401 both ways; live expiry (psql); audit emission |

## Coverage → PC-05 acceptance criteria (filled during synthesis)

| AC | Criterion | Dynamic proof |
|---|---|---|
| ⭐ PC-05-AC2 | consent: only the company approve produces `approved` | _pending_ |
| ⭐ PC-05-AC3 | cross-tenant: other org's grant invisible + approve → 404 | TC-BG-010 (inbox org-scoped), TC-BG-011 (cross approve→404, untouched), TC-BG-012 (cross deny+revoke→404, org filter precedes state guard) — all ✅ |
| ⭐ PC-05-AC4 | state machine → 409 | _pending_ |
| ⭐ PC-05-AC4b | concurrent transitions serialize (row lock) | _pending_ |
| PC-05-AC5 | platform revoke is requester-scoped (404 for another's) | TC-BG-013 (list requester-scoped, positive control), TC-BG-014 (revoke requester-scoped: demo→404, requester→200) — both ✅ |
| ⭐ PC-05-AC6 | audience confinement (401 both ways) | _pending_ |
| PC-05-AC7 | live expiry (clock, not stored flag) | _pending_ |
| PC-05-AC8 | every transition logged; `support.approved` carries `expires_at` | _pending_ |
