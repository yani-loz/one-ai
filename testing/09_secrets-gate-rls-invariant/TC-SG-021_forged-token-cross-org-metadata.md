# TC-SG-021: Forged dev-secret platform token yields HTTP 200 cross-org metadata (blast radius unchanged by f8a4fbd)

| Field | Value |
|---|---|
| **ID** | TC-SG-021 · **Suite** C · **Type** Adversarial · **Severity if fail** High (if undocumented) |
| **Result** | ⚠️ Pass-with-concern · **Tag** 📋 CONFIRMS-DOCUMENTED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** if the running dev stack's JWT secret were not forgeable, a self-minted platform token (random `sub`,
signed with the public dev secret) would be rejected. If `GET /platform/orgs` returns 200 with multi-org data, the dev
secret is forgeable and the `f8a4fbd` boot gate does nothing for the running stack.

**Command** — in-container; positive forgery + negative control (unknown secret)
```
jwt.encode({'sub': <random-uuid>, 'type':'access', 'aud':'platform', 'role':'platform_admin', 'exp': now+3600},
           'dev-only-insecure-secret-change-me-in-prod', 'HS256')
# GET http://localhost:8000/platform/orgs with Authorization: Bearer <tok>
# control: same claims signed with an UNKNOWN secret
```
**Evidence**
```
Live settings (get_settings()): app_env=local, jwt_secret=dev-only-...-prod, HS256, requires_secure_secrets=False
Forged sub (not a real admin row): 08e6c251-44e4-4e70-ae2a-0a415a8daa74
HTTP_STATUS: 200 | ORG_COUNT: 2 | DISTINCT_ORG_IDS: [globex, demo]
RAW_BODY fields = [created_at, id, name, slug, status, user_count]   ← metadata only, NO tenant content
NEGATIVE CONTROL (unknown secret): HTTP 401 {"detail":"Access token is invalid."}
```
**Verdict:** The dev stack's JWT secret IS forgeable: a token with a random non-existent `sub` + the public dev secret
passes `get_current_platform_admin` (no DB lookup on `/platform/orgs`) and returns 200 with both orgs. This is cross-org
**metadata** (id/name/slug/status/user_count/created_at) — correctly NOT tenant content, per the platform-admin contract;
the content-level bypass is TC-SG-020. The negative control isolates the cause to the public dev secret, not a missing auth
check. The forged-token blast radius is exactly as documented (`FIX_BEFORE_PROD.md:46`; prior TC-PC-025 / TC-ER-033) and is
UNCHANGED by `f8a4fbd` — the gate fires only at boot in non-`{local,test}` envs.
