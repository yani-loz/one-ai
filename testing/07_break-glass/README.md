# Target 07 — Break-glass support access (PC-05) — Adversarial Validation

> Dynamic, adversarial validation of the **PC-05 break-glass** grant lifecycle against the **live stack**:
> a platform admin **requests** time-boxed access; a **company_admin of that tenant must approve** (consent);
> the grant is time-boxed (live expiry) and every step is logged. Companion to
> `docs/audits/2026-06-01_break-glass-dynamic-adversarial.md` + the static review
> `docs/audits/2026-06-01_platform-break-glass-pr5-review.md` (6 fixed, 0 functional defects). Case code
> **`BG`** (`TC-BG-NNN`).

## Environment

- Live stack `:8000`; harness inside the backend container, self-contained over stdin:
  `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/<script>.py | docker compose exec -T backend python -`
- psql ground-truth on the **db** container. Demo platform admin onboards fresh orgs; **never mutated**.
- **HARD RULE:** never act on demo/globex; provision your own run-stamped orgs (`provision_company`).

## Status dashboard

> Result: ✅ pass · ❌ fail (a defect/the win) · ⚠️ pass-with-concern. Tag: 🆕 NEW · ✔ CONFIRMS-FIXED ·
> ✖ REFUTES-FIX · 📋 CONFIRMS-DOCUMENTED · — n/a.
> **Run 2026-06-01.** **20 cases · 19 ✅ · 1 ⚠️ · 0 ❌ · 0 🆕.** Matches the PR-5 review (0 functional/security
> defects). The one ⚠️ is the documented dev-secret blast radius (TC-BG-003 forged consent).

| Suite | Cases | Result spread | NEW | Notes |
|---|---|---|---|---|
| CONSENT — approval path + forged-token | TC-BG-001..004 | 3 ✅ · 1 ⚠️ | 0 | request starts `requested`; no platform approve path; **forged `company_admin` self-approves (⚠️ documented)**; real approve attributes decider + 4h box |
| ISO — cross-tenant + requester-scope | TC-BG-010..014 | 5 ✅ | 0 | inbox org-scoped; cross approve/deny/revoke → 404 (no existence leak); platform list+revoke requester-scoped (positive controls) |
| STATE — transition machine + row-lock | TC-BG-020..024 | 5 ✅ | 0 | 409 matrix (approve-twice/deny-terminal/revoke-terminal/approve-then-deny); 50× concurrent approve+revoke → all 50 `revoked` (psql), no lost update |
| AUDIENCE+EXPIRY+AUDIT | TC-BG-030..035 | 6 ✅ | 0 | 401 both ways; live expiry (psql backdate → is_active=false); expiry terminal; all 4 `support.*` logged; reason 422 / injection-literal |

## Coverage → PC-05 acceptance criteria

| AC | Criterion | Dynamic proof |
|---|---|---|
| ⭐ PC-05-AC2 | consent: only the company approve produces `approved` | ✅ TC-BG-002 (no platform approve path), TC-BG-004 (decider attributed); ⚠️ TC-BG-003 (forged company token manufactures consent) |
| ⭐ PC-05-AC3 | cross-tenant: other org's grant invisible + approve → 404 | ✅ TC-BG-010/011/012 (existence-oracle-safe, org filter precedes state guard) |
| ⭐ PC-05-AC4 | state machine → 409 | ✅ TC-BG-020/021/022/023 |
| ⭐ PC-05-AC4b | concurrent transitions serialize (row lock) | ✅ TC-BG-024 (50× → all `revoked`, no lost update; corroborates `FOR UPDATE`) |
| PC-05-AC5 | platform revoke is requester-scoped (404 for another's) | ✅ TC-BG-013/014 (positive controls + audit trace) |
| ⭐ PC-05-AC6 | audience confinement (401 both ways) | ✅ TC-BG-030 |
| PC-05-AC7 | live expiry (clock, not stored flag) | ✅ TC-BG-031 (backdate → is_active=false); TC-BG-032 (expiry terminal) |
| PC-05-AC8 | every transition logged; `support.approved` carries `expires_at` | ✅ TC-BG-033 |

> **Provenance note:** these per-case files were rebuilt from the workflow output (the original
> agent-authored scaffolds were removed by repo cleanup `3966800`). Each carries its raw evidence + verdict;
> the consolidated narrative is the audit doc.
