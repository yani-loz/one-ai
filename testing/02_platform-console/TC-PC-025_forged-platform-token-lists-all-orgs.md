<!--
  XDOM suite — cross-domain confinement. See ../README.md for legend/tags.
-->

# TC-PC-025: Forged platform token lists ALL orgs' metadata (cross-customer exposure)

| Field | Value |
|---|---|
| **ID** | TC-PC-025 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (FORGED) |
| **Severity if it fails** | Critical (accepted/tracked — FIX_BEFORE_PROD: Rotate JWT_SECRET) |
| **Status** | Executed |
| **Result** | ❌ Fail (attack succeeded — cross-customer metadata read; documented/tracked) |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Demonstrate the READ side of the forged-token exposure: a platform-aud token forged with the
dev secret (random sub) can call `GET /platform/orgs` and read the **entire fleet's** metadata —
every customer org's id/name/slug/status/user_count/created_at, including orgs the attacker
never created. Same root cause as TC-PC-024 (JWT secret is the single isolation layer).

## Break hypothesis
The forged token passes `decode_access_token`, the platform gate does no existence check, and
`list_organizations` returns all rows — so the response contains FOREIGN org slugs (the seeded
`demo`/`globex` and other suites' run-stamped orgs). Expected: **200 with foreign metadata** —
the documented exposure. (A live control would be RLS or a least-privilege role; both are
inert.)

## Preconditions
- Live stack up (dev JWT secret in effect); the seeded `demo` + `globex` orgs exist and are
  guaranteed foreign to this suite (slug does NOT start with `xdom-`).
- Forged token via `forge_platform_token()`.

## Steps
1. Forge a platform-aud token (random sub, dev secret).
2. `GET /platform/orgs` with the forged token.
3. Filter the returned slugs for any NOT starting with `xdom-` → foreign-org exposure.

## Expected result
- `200` with a list of `{id,name,slug,status,user_count,created_at}` (exactly the 6 metadata
  fields — no tenant content) containing ≥1 foreign org slug.

## Harness
Script: `harness/tc_025.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_025.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ❌ Fail — the attack **succeeded** (forged token read 15 foreign customers' metadata). A FAIL is the win in this suite; the exposure is documented/tracked (hence CONFIRMS-DOCUMENTED), but a forged dev-secret token reading every customer's existence + headcount violates cross-customer confinement. (The metadata-only response shape limits blast radius — see Verdict — but the read itself succeeded.)
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> The forged token listed 18 orgs' metadata; 15 were foreign to this run — including the seeded
> `demo` and `globex` customer orgs and other suites' run-stamped orgs. The response shape was
> exactly the 6 metadata fields (no tenant content leaked beyond metadata). A forged dev-secret
> token sees every customer's existence + headcount.

**Evidence**

```
== TC-PC-025 — FORGED platform token lists ALL orgs' metadata (cross-customer exposure) ==
[forge]   forged platform token w/ random sub on DEV_SECRET
[attack]  GET /platform/orgs (FORGED platform token): 200
          count = 18 fields = ['created_at', 'id', 'name', 'slug', 'status', 'user_count']
          sample slugs (foreign to this run) = ['demo', 'globex', 'probe-19e825aafdc0369', 'probe-19e826153dc4763', 'probe-19e82616433e693', 'probe-19e826198d60590', 'onb40-19e8261d51df7aa', 'onb41-19e826234c50f5e', 'onb42-19e82623a4993f4-a', 'onb43-19e8262728847da-a']
          field-shape EXACTLY metadata? True
RESULT: PASS (DEFECT-AS-DESIGNED) — forged token saw 15 foreign orgs' metadata
```

**Verdict**

A forged dev-secret platform token reads the whole fleet's metadata — **as documented**. Root
cause identical to TC-PC-024: `get_current_platform_admin` (`dependencies.py:103-117`) trusts
the token alone, and `list_organizations` (`platform_auth_service.py:194`) returns all rows with
no per-caller scoping (correct *by design* — platform admin is global). The exposure is gated
solely by JWT secret secrecy. Note the response is *correctly* metadata-only (the 6 fields, no
tenant content) — so this case ALSO positively confirms the FIX_BEFORE_PROD "metadata only"
content-blindness invariant holds even on this path. The exposure itself is tracked under
`docs/FIX_BEFORE_PROD.md` → "Rotate `JWT_SECRET`". CONFIRMS-DOCUMENTED, Critical-if-not-documented.

**Notes / follow-up**
Read twin of TC-PC-024 (forged-token WRITE). Remediation: rotate `JWT_SECRET` + boot-time guard;
the metadata-only shape (no content) is the secondary mitigation that limits blast radius to
existence + headcount, not tenant data.
