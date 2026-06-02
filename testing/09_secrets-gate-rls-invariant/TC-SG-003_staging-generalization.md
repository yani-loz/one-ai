# TC-SG-003: Staging generalization — staging + dev JWT RAISES (the headline gap the broadened gate closes)

| Field | Value |
|---|---|
| **ID** | TC-SG-003 · **Suite** A · **Type** Positive · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** a prior production-only gate (`app_env=='production'`) let staging boot while signing tokens with the
public dev key. If the broadened `requires_secure_secrets` regressed to production-only, staging would slip through.

**Command**
```
docker compose exec -T backend python - <<'PY'
Settings(app_env='staging', jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='a-strong-real-pw')
PY
```
**Evidence**
```
[003-staging-devJWT] RAISED InsecureConfigurationError: Refusing to start with app_env='staging' while using insecure
default secret(s): JWT_SECRET. ... Only app_env 'local' or 'test' may use the dev defaults.
```
**Verdict:** Defense held. Staging now fails closed on the dev JWT secret — the whole-of-non-dev generalization works
(`requires_secure_secrets` is True for everything outside `{local,test}`). This is the specific gap the broadened gate closed.
