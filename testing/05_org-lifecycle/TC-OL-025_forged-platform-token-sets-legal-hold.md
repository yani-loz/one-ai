# TC-OL-025: Forged platform token sets a legal hold (compliance blast radius)

| Field | Value |
|---|---|
| **ID** | TC-OL-025 · **Target** Org Lifecycle (PC-03a) — `PATCH …/legal-hold` · **Suite** XDOM ⭐ |
| **Type** | Adversarial (FORGED) · **Severity if fail** Critical (tracked) · **Status** Executed |
| **Result** | ❌ Fail (attack succeeded — documented) · **Finding tag** CONFIRMS-DOCUMENTED |

## Objective
Same root as TC-OL-024, on the legal-hold flag: a forged dev-secret platform token can place a legal hold on
any org — a compliance-tampering primitive (a legal hold will block erasure once PC-06 lands).

## Steps / Harness
`provision_company("xdom025")` → forged platform token (`forge_platform_token()`) → `PATCH .../legal-hold
{true}` → read-back via the real platform token → clear. `harness/_finish_suspend.py` (case 025).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ❌ Fail (documented) · **Tag:** CONFIRMS-DOCUMENTED

**Evidence**
```
[025] FORGED platform token PATCH legal-hold true=200 (200); read-back legal_hold=True (True)
```

**Verdict**
Attack succeeded → ❌ (the win). Identical mechanism to TC-OL-024 (the forged platform-aud token passes
`get_current_platform_admin`; RLS inert ⇒ JWT secret is the only control). On the legal-hold surface the
blast radius is **compliance**: an attacker can set/clear holds on any tenant, which (per PC-06) will
beat right-to-erasure. Same tracked fix — *Rotate `JWT_SECRET`*. CONFIRMS-DOCUMENTED. Hold cleared after the test.

**Notes** Twin of TC-OL-024 (status). The audience gate is correct — a forged *company*-aud token is 401 (TC-OL-022).
