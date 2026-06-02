<!--
  Test-case template. Copy this file to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md
  and fill every section. Author the top half BEFORE running; write the
  "Execution result" block back into this same file AFTER running.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-ER-033: A forged platform token reads any org's compliance export (audit trail + metadata) — documented read blast radius

| Field | Value |
|---|---|
| **ID** | TC-ER-033 |
| **Target** | GDPR erasure + compliance export (PC-06) |
| **Suite** | AUTHZ — audience confinement + forged-token blast radius |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Characterize the read-side blast radius of the dev-secret deferral: a **forged platform-aud token**
(random sub, dev secret) drives `GET /platform/orgs/{id}/compliance-export` → **200**, disclosing an org's
full audit trail + metadata. The export has NO password re-auth (unlike erase — see TC-ER-032), so the
forged read succeeds. Same documented root as the erase forgery: "Rotate `JWT_SECRET`". Bound it: the
export is metadata/trail only (counts, actions, actor emails IN the audit_log) — never tenant content — so
the *read* blast radius is narrower than a *destroy* radius.

## Break hypothesis
The compliance-export endpoint is gated only by `get_current_platform_admin`; with the dev secret the gate
is forgeable, and (unlike erase) there is no second credential factor. So a forged platform token can pull
any org's audit history (who did what, when, requester emails) without being a real admin. The bet:
signature+audience+expiry is the whole gate for the read path, and that gate is defeated by a known dev
secret.

## Preconditions
- Live stack `:8000`. Suite code **AUTHZ**, run-stamped slug (lowercase `[a-z0-9-]`).
- Read-only (export does not mutate). Still target ONLY a fresh AUTHZ org onboarded THIS run via
  `provision_company` — never demo/globex/another suite's org.
- `forge_platform_token()` (random sub, dev secret, valid exp).

## Steps
1. `provision_company("authz-fexp-…")` → fresh org `{org_id, slug}` (has onboarding + login audit rows).
2. `forge_platform_token()` (random sub, aud=platform, dev secret, valid exp).
3. `GET /platform/orgs/{our org}/compliance-export` with the forged token → expect 200.
4. Inspect the body: `organization` metadata block + non-empty `audit` list. Confirm metadata/trail only
   (no tenant content field).

## Expected result
- **200** with `ComplianceExportResponse`: an `organization` metadata object (id/name/slug/status/
  user_count/legal_hold/created_at) + an `audit` array of trail entries + `generated_at`. No tenant content.

## Harness
Script: `harness/tc_033.py` · run: `cat testing/08_erasure/harness/_common.py testing/08_erasure/harness/tc_033.py | docker compose exec -T backend python -`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** 2026-06-01 18:43 local (live stack)
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> A platform-aud token forged from the dev secret with a random `sub` was accepted (**200**) by the
> compliance-export endpoint and returned the org's metadata block plus its full audit trail (2 entries:
> onboarding + admin login). The body is metadata + audit entries only — keys `["audit","generated_at",
> "organization"]`; no tenant content. Behaves exactly as the documented dev-secret threat model predicts;
> the read is broad over the trail but content-blind. Unlike the erase path (TC-ER-032), there is no
> password gate, so the forged read is not blocked. psql confirms the org was untouched by the read
> (`active`, 1 user, 0 `org.erased`).

**Evidence**

```
# harness stdout
ORG authz-fexp-19e847fbe424cd9 60c42021-3e34-4f73-8ba4-dbacdc7b1430
EXPORT_STATUS 200
EXPORT_KEYS ['audit', 'generated_at', 'organization']
ORGANIZATION {'id': '60c42021-...', 'name': 'Org authz-fexp-19e847fbe424cd9',
              'slug': 'authz-fexp-19e847fbe424cd9', 'status': 'active', 'user_count': 1,
              'legal_hold': False, 'created_at': '2026-06-01T18:43:45.175354Z'}
AUDIT_LEN 2
AUDIT_FIRST {'action': 'auth.login.success', 'actor_type': 'user',
            'actor_email': 'admin-authz-fexp-19e847fbe424cd9@oneai.dev', 'ip_address': '127.0.0.1', ...}
HAS_GENERATED_AT True

# psql ground-truth (db container) — untouched by the read
           slug            | status | users | erased_rows
---------------------------+--------+-------+-------------
 authz-fexp-19e847fbe424cd9 | active |     1 |           0
```

**Verdict**

Behaves **as designed under the documented deferral**. The export endpoint
(`erasure_routes.py:47-56` → `ErasureService.export_compliance`, `erasure_service.py` `export_compliance`)
is gated solely by `get_current_platform_admin`, which — with the dev-default `JWT_SECRET` — a forged token
defeats (`tokens.py:77-83`). **Read blast radius: a leaked/known dev secret lets anyone read any org's full
audit trail + metadata.** Critically narrower than a destroy radius because the response is metadata/trail
only (content-blind by design — schema docstring `erasure_schemas.py`), so no tenant *content* leaks even
though the access-history does. Same SINGLE tracked root: "Rotate `JWT_SECRET`" (`docs/FIX_BEFORE_PROD.md`).
Tagged CONFIRMS-DOCUMENTED — the forged-read capability IS the disclosed dev-secret deferral (not a new
leftover). Pass-with-concern records the documented exposure caveat.

**Notes / follow-up**

Destroy-side counterpart: TC-ER-032, where the SAME forged token is BLOCKED (403) by a sudo password
re-auth — so the live stack exhibits a sharp asymmetry: **a leaked dev secret can READ any org's audit
trail (here) but cannot DESTROY a tenant (TC-ER-032).** Remediation: rotate `JWT_SECRET`, fail boot on the
default in production, move secrets to a manager (`docs/FIX_BEFORE_PROD.md`). Consider a second factor on
the export path too, to close the read side.
