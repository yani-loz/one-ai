# Break-glass support access (PC-05) — Dynamic Adversarial Validation

> **Status as of 2026-09-06 (dated record — the findings below are unchanged):** the scaffold-loss note in the Scope block (*"the per-case `testing/07_break-glass/TC-BG-*.md` scaffolds were removed by the repo owner (commit `3966800`) … the durable evidence now lives in the workflow output + this consolidated audit"*, repeated at §4 "Scaffold removed") **no longer holds.** Commit `b5bee33` *"revert(testing): restore the dynamic-adversarial QA passes I wrongly removed in 3966800"* (2026-06-02) restored the whole suite **in the same commit that added this audit document**, and it is tracked today: `git ls-files testing/07_break-glass` → 22 files (20 `TC-BG-*.md` cases + `README.md` + `harness/_common.py`). The per-case evidence is therefore in the repo, not only in the workflow output.

> **Scope:** the PC-05 break-glass grant lifecycle (`feat/platform-break-glass`, migration `0006`) — platform
> request/list/revoke + company inbox/approve/deny/revoke. Dynamic complement to
> `2026-06-01_platform-break-glass-pr5-review.md` (6 findings fixed, 0 functional defects) and
> `EPIC-PC-05-break-glass.md`.
>
> **Method:** a 4-suite `Workflow` (CONSENT / ISO / STATE / AEA) ran **20 cases** against the live stack
> (real uvicorn `:8000`, psql + audit-trail ground-truth). 4 agents, ~441k tokens. The headline
> (forged-token consent manufacture) was lead-verified first-hand in the harness probe.
> **Note:** the per-case `testing/07_break-glass/TC-BG-*.md` scaffolds were removed by the repo owner
> (commit `3966800`, "remove stray review-agent QA scaffolds"); the durable evidence now lives in the
> workflow output + this consolidated audit.

## 1. Executive summary

**20 cases · 19 ✅ · 1 ⚠️ · 0 ❌ · 0 🆕.** PC-05 is **sound** — matching the static review. Every consent /
isolation / state-machine / audience / expiry / audit contract held live and discriminatingly. The single ⚠️
is the documented dev-secret blast radius, not a new defect.

The standout — **consent is structurally enforced *and* its one weak point is precisely characterized:**
there is no platform approve path (TC-BG-002, verified against the live OpenAPI: the only approve route is
company-side; a `/platform/.../approve` probe → 404), so a platform admin cannot self-approve. **But a forged
dev-secret `company_admin` token manufactures the customer's consent (TC-BG-003 → 200 approved):** with the
forgeable dev `JWT_SECRET` an attacker mints a `company_admin` token for any `org_id` and calls the company
approve endpoint, *manufacturing* the exact consent break-glass promises ("access only if we say yes"), with
an unaccountable phantom decider (`decided_by_email=null`). Root: dev secret forgeable + RLS inert ⇒ the JWT
signature is the single isolation layer. Same `Rotate JWT_SECRET` + `Enforce RLS` deferral — **its most
consequential surface.** CONFIRMS-DOCUMENTED (not NEW); the phantom attribution is a downstream effect.

## 2. What held (live, psql/audit-corroborated)

| Suite | Cases | Result |
|---|---|---|
| CONSENT | TC-BG-001..004 | ✅ request opens strictly `requested`/`is_active=false`, requester email persisted; no platform approve path; a *real* approve attributes the decider (email+id) + sets exactly the 4.0h box — the deliberate contrast to the forged phantom (TC-BG-003 ⚠️) |
| ISO | TC-BG-010..014 | ✅ inbox org-scoped; cross-org approve/deny/revoke → **404** byte-identical to a nonexistent grant (existence-oracle-safe), grant untouched (positive-control transition + audit shows only the legit actor); the org filter fires **before** the state guard (cross-org revoke of an *approved* grant is 404, not 409); platform list+revoke requester-scoped (a second admin's grant absent + revoke → 404, zero audit trace) |
| STATE | TC-BG-020..024 | ✅ 409 matrix (approve-twice → no time-box re-extension; `denied`/`revoked` terminal; approve-then-deny → 409); **50× concurrent approve+revoke → all 50 end `revoked`** (psql GROUP BY: 0 approved, 0 active), no 5xx — the lost-update TOCTOU does not reproduce |
| AEA | TC-BG-030..035 | ✅ audience 401 both ways (before any state change); **live expiry** (psql backdate `expires_at` → `is_active=false`, `status` stays `approved`); **expiry terminal** (re-approve an expired grant → 409, window can't reopen); all four `support.*` logged, `support.approved` carries `expires_at`+`window_hours=4.0`; reason empty/>500/extra → 422; SQL-injection reason stored literally (table intact, content-blind) |

## 3. The documented deferral (the ⚠️ win)

**Forged dev-secret `company_admin` token manufactures customer consent — Critical (tracked).** *TC-BG-003,
PASS_WITH_CONCERN / CONFIRMS-DOCUMENTED, lead-verified.* Break-glass guarantees "Ethera staff reach a tenant
only if the customer says yes." The platform service has no approve method, so consent must be company-side —
but a forged `company_admin` token (dev secret, any `org_id`) calls the company approve endpoint → **200,
`approved`, `is_active=true`, +4h**, manufacturing the consent, unaccountable (`decided_by_email=null`). Same
root as `FIX_BEFORE_PROD` → *Rotate `JWT_SECRET`* + *Enforce RLS*. **This is one of three forged-token
write capabilities this session's passes proved on the same root** (with forged suspend, TC-OL-024, and —
distinctly — forged *erase* now blocked by the new sudo-reauth, TC-ER-032).

## 4. Coverage & limitations

- **Backend only.** PC-05b's **frontend** (platform request panel + company HITL approval inbox with the
  `animate-clari-pulse` glow) is a separate Playwright pass.
- **Same-row race = corroboration** (TC-BG-024): serializes on the `FOR UPDATE` lock by design.
- **Forward hook:** `grant_is_active` is the seam a future content read must gate on; today no content
  endpoint exists, so an active grant unlocks nothing.
- **Scaffold removed:** the per-case `testing/07` files were deleted by the owner mid-session; findings
  preserved here + in the workflow output.
