<!--
  XDOM suite — cross-domain confinement. See ../README.md for legend/tags.
-->

# TC-PC-024: Forged platform token (dev secret, random sub) onboards a fresh org (201)

| Field | Value |
|---|---|
| **ID** | TC-PC-024 |
| **Target** | Platform Console (`/platform/*`) |
| **Suite** | XDOM — cross-domain confinement ⭐ |
| **Type** | Adversarial (FORGED) |
| **Severity if it fails** | Critical (accepted/tracked — FIX_BEFORE_PROD: Rotate JWT_SECRET) |
| **Status** | Executed |
| **Result** | ❌ Fail (attack succeeded — defect demonstrated; documented/tracked) |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Demonstrate the **single point of failure**: with the forgeable dev JWT secret, anyone can mint
a valid platform-aud token (with a RANDOM, non-existent sub) and exercise **full platform
write power** — `POST /platform/orgs` succeeds (201) and commits a real org + company_admin.
`get_current_platform_admin` verifies only the token signature/audience/expiry, never that the
admin row exists. Isolation rests entirely on JWT secret secrecy (RLS is inert; the app
connects as superuser).

## Break hypothesis
The "attacker's bet" here *succeeds by design*: the forged token is signed with `DEV_SECRET`
(`dev-only-insecure-secret-change-me-in-prod`, the production default until rotated), so it
passes `decode_access_token`. The platform gate does no admin-existence check, so onboarding
proceeds and commits. Expected: **201 success** (the defect), tying directly to
`FIX_BEFORE_PROD.md` → "Rotate JWT_SECRET".

## Preconditions
- Live stack up (dev JWT secret in effect).
- Forged token via `forge_platform_token()` (random sub, aud='platform', DEV_SECRET).
- The forged token bypasses `provision_company`'s auto-namespacing → slug/email namespaced
  **manually**: `slug=xdom-forged-<stamp>`, `admin_email=admin-xdom-forged-<stamp>@oneai.dev`.

## Steps
1. Forge a platform-aud token with a random sub on the dev secret.
2. `POST /platform/orgs` (run-stamped org) with the forged token.
3. Verify in the DB (psql) that the org + admin committed.

## Expected result
- `201` with the full `{organization{6 fields}, admin{UserResponse}}` body. The org persists
  in the DB. (This is the *documented* exposure, not a contract violation of an active control.)

## Harness
Script: `harness/tc_024.py` · run: `cat testing/02_platform-console/harness/_common.py testing/02_platform-console/harness/tc_024.py | docker compose exec -T backend python -`
psql ground-truth: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT o.slug,o.status,u.email,u.role FROM organizations o JOIN users u ON u.org_id=o.id WHERE o.slug LIKE 'xdom-forged-%';"`

---

## Execution result

- **Run at:** 2026-06-01 (local)
- **Result:** ❌ Fail — the attack **succeeded** (forged token committed a real org). A FAIL is the win in this suite; the exposure is documented/tracked (hence the CONFIRMS-DOCUMENTED tag), but the contract "only a real platform admin can onboard" was violated.
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> A forged platform token with a random, non-existent sub successfully onboarded a real org and
> its first company_admin (201). psql confirms both rows committed to the live DB. No admin
> account backed the token — the platform gate verified only the token, granting full write power.

**Evidence**

```
== TC-PC-024 — FORGED platform token (random sub, dev secret) onboards an org (201) ==
[forge]   forged platform token w/ random sub on DEV_SECRET
[attack]  POST /platform/orgs (FORGED platform token): 201
          body: {'organization': {'id': 'dc1d428f-d668-4332-83e9-6bf811586a6c', 'name': 'Org xdom-forged-19e826508dbe9f6', 'slug': 'xdom-forged-19e826508dbe9f6', 'status': 'active', 'user_count': 1, 'created_at': '2026-06-01T08:55:20.042165Z'}, 'admin': {'id': '49640118-2284-429e-a5dc-bb383e4ef70a', 'email': 'admin-xdom-forged-19e826508dbe9f6@oneai.dev', 'full_name': 'Forged Onboard Admin', 'role': 'company_admin', 'is_active': True, 'org_id': 'dc1d428f-d668-4332-83e9-6bf811586a6c', 'created_at': '2026-06-01T08:55:20.042165Z'}}
RESULT: PASS (DEFECT-AS-DESIGNED) — forged dev-secret token created org slug=xdom-forged-19e826508dbe9f6
CLEANUP-NOTE: org slug xdom-forged-19e826508dbe9f6 left in DB (do not delete); run-stamped, isolated.

-- psql ground-truth --
            slug             | status |                    email                    |     role
-----------------------------+--------+---------------------------------------------+---------------
 xdom-forged-19e826508dbe9f6 | active | admin-xdom-forged-19e826508dbe9f6@oneai.dev | company_admin
(1 row)
```

**Verdict**

The forged token granted full platform write power — **as documented**. Root cause: the dev
JWT secret is the forgeable default and `get_current_platform_admin`
(`backend/app/identity/dependencies.py:103-117`) verifies only signature+audience+expiry, never
that the admin exists (`onboard_organization` in `platform_auth_service.py:141` runs purely on
the verified-token assumption). With RLS inert (app connects as superuser → bypass), the JWT
secret is the **single** isolation layer. This is exactly the exposure tracked in
`docs/FIX_BEFORE_PROD.md` → **"Rotate `JWT_SECRET` — the dev default must never reach prod"**
(and "fail boot if it is still the default when app_env == 'production'"). CONFIRMS-DOCUMENTED,
Critical-if-not-documented.

**Notes / follow-up**
Same root cause as TC-PC-025 (forged-token READ exposure). Remediation: rotate `JWT_SECRET` to a
strong per-env secret + boot-time guard. The org is left in the DB intentionally (run-stamped,
isolated) — cleanup note only, do not delete.
