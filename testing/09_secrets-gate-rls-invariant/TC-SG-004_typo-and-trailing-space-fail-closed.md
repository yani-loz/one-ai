# TC-SG-004: Typo / unknown envs fail closed — incl. trailing-space 'Production ' (.lower() applied, .strip() NOT)

| Field | Value |
|---|---|
| **ID** | TC-SG-004 · **Suite** A · **Type** Adversarial · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** an unrecognized/typo'd `app_env` might fall through to the dev exemption and boot on the public
secret. Specifically, trailing whitespace `'Production '` after `.lower()` but without `.strip()` must NOT normalize to a
known value, and must not be treated as exempt.

**Command**
```
docker compose exec -T backend python - <<'PY'
for env in ['prod', 'Production ', 'prdouction']:
    Settings(app_env=env, jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='oneai')
PY
```
**Evidence**
```
[004-'prod']         RAISED: ... insecure default secret(s): JWT_SECRET, POSTGRES_PASSWORD ...
[004-'Production ']  RAISED: ... insecure default secret(s): JWT_SECRET, ...
[004-'prdouction']   RAISED: ... insecure default secret(s): JWT_SECRET, ...
```
**Verdict:** Defense held — fails closed on all three. `'Production '` is treated as unknown because the code lower-cases
but does not strip, so `' production '` is not in `{local,test}` ⇒ `requires_secure_secrets` True ⇒ RAISE. Unknown env
defaults to secure-required, the correct fail-closed posture.
