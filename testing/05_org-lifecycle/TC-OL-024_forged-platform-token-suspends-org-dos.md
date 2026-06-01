<!--
  XDOM suite — cross-domain confinement + forged-token blast radius on the new write
  endpoints. See ../README.md (testing/README.md) for legend/tags.
-->

# TC-OL-024: Forged platform token (dev secret, random sub) suspends an org (200) — availability-kill

| Field | Value |
|---|---|
| **ID** | TC-OL-024 |
| **Target** | Org Lifecycle (PC-03a) — `PATCH /platform/orgs/{id}/status` |
| **Suite** | XDOM — forged-token blast radius ⭐ |
| **Type** | Adversarial (FORGED) |
| **Severity if it fails** | Critical (accepted/tracked — FIX_BEFORE_PROD: Rotate JWT_SECRET) |
| **Status** | Executed |
| **Result** | ❌ Fail (attack succeeded — defect demonstrated; documented/tracked) |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Demonstrate the **single point of failure** on the new write surface: with the forgeable dev JWT
secret, anyone can mint a valid platform-aud token (RANDOM, non-existent `sub`) and **suspend**
an org via `PATCH …/status` — which the auth gate then enforces as a login block. The platform
gate verifies only signature/audience/expiry, never that the admin row exists. Because RLS is
inert (the app connects as superuser) the JWT secret is the **single** isolation layer; a leaked
dev secret is a platform-wide availability kill (suspend every customer).

## Break hypothesis
The attacker's bet *succeeds by design*: the forged token is signed with `DEV_SECRET`
(`dev-only-insecure-secret-change-me-in-prod`, the production default until rotated), so it passes
`decode_access_token(..., PLATFORM_AUDIENCE)`. `get_current_platform_admin` does no
admin-existence check, so `set_status(suspended)` runs and the session commits. Expected: **200**,
the org flips to `suspended`, and a real company login on that org is then **403** — proving the
forged write reached the auth gate end-to-end. Ties directly to `FIX_BEFORE_PROD.md` → "Rotate
JWT_SECRET".

## Preconditions
- Live stack up (dev JWT secret in effect).
- A run-stamped, freshly-provisioned target org (status `active`) via `provision_company` —
  **OURS**, safe to suspend/reactivate. Its pre-suspension company admin creds drive the
  login-gate proof.
- Forged token via `forge_platform_token()` (random sub, aud='platform', DEV_SECRET).
- **Cleanup is load-bearing:** after the proof, reactivate the org with the REAL platform token
  and read back `status=active` so the run-stamped org is not left stranded.

## Steps
1. `provision_company` → target org (active) + its company admin creds.
2. Forge a platform-aud token with a random sub on the dev secret.
3. `PATCH /platform/orgs/{id}/status {"status":"suspended"}` with the forged token → expect 200.
4. Company login on the target org with valid creds → expect 403 (the forged write reached the
   gate). psql ground-truth: `status=suspended`.
5. **Reactivate** with the REAL platform token; read back `status=active`; company login → 200.

## Expected result
- Step 3: `200` with the detail body, `status=="suspended"` (the defect — forged write succeeds).
- Step 4: company login `403` (`{"detail":"Your organization's access is suspended."}`); DB
  `status=suspended`.
- Step 5: reactivate `200`, read-back `status=active`, login `200` (org restored — no strand).

## Harness
Script: `harness/tc_024.py` · run: `cat testing/05_org-lifecycle/harness/_common.py testing/05_org-lifecycle/harness/tc_024.py | docker compose exec -T backend python -`
psql ground-truth: `docker compose exec -T db psql -U oneai -d oneai -c "SELECT slug,status FROM organizations WHERE slug LIKE 'xdom-%' ORDER BY created_at DESC LIMIT 1;"`

---

## Execution result

- **Run at:** 2026-06-01 local (lead finisher — the workflow agent authored this case but was cut off before recording)
- **Result:** ❌ Fail (attack succeeded — defect demonstrated; documented/tracked)
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> A platform-aud token forged with the dev secret and a RANDOM non-existent `sub` suspended a real org via
> `PATCH .../status` (200). A real company login on that org was then refused (403) — the forged write
> reached and drove the company auth gate. Reactivating with the REAL platform token restored login (200),
> so the run-stamped org was not stranded.

**Evidence**

```
[024] FORGED platform token PATCH suspended=200 (200) -> real login now=403 (403)
      => forged write reached the auth gate; reactivated via REAL token=200 login restored=200 (200)
      org=20825ef1-baea-4742-b7bb-b8169f2e2f37
```

**Verdict**

Attack SUCCEEDED → ❌ (the win). `get_current_platform_admin` (`dependencies.py:103-117`) verifies only the
JWT signature + `aud='platform'` + expiry — it never checks the admin row exists — and RLS is inert (the app
connects as superuser), so the dev `JWT_SECRET` is the **single** isolation layer. A leaked/forged dev-secret
token can therefore suspend **any** org via the new write endpoint, and the suspension is enforced as a real
login block (403). Blast radius on this surface: **platform-wide availability kill** (suspend every customer)
and, via TC-OL-025, **compliance tampering** (set legal holds). Same root as the tracked
`FIX_BEFORE_PROD.md` → *Rotate `JWT_SECRET`* (+ *Enforce RLS*); CONFIRMS-DOCUMENTED, now demonstrated on the
PC-03a write surface.

**Notes / follow-up**

Org reactivated and verified `active` (no strand). Closing *Rotate JWT_SECRET* de-fangs this; the audience
gate itself is correct (a forged **company**-aud token is rejected — TC-OL-021).
