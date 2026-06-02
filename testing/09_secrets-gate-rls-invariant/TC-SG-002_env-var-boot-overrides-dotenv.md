# TC-SG-002: End-to-end env-var boot fails closed; injected APP_ENV=production overrides .env (exit 1)

| Field | Value |
|---|---|
| **ID** | TC-SG-002 · **Suite** A · **Type** Positive · **Severity if fail** Critical |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the real boot path (env vars, not kwargs) might fail open — the container's `.env app_env=local`
could win over an injected `APP_ENV=production`, exempting the process; or the validator runs but the process exits 0.

**Command**
```
docker compose exec -T -e APP_ENV=production -e JWT_SECRET=dev-only-insecure-secret-change-me-in-prod \
    -e POSTGRES_PASSWORD=a-strong-real-pw backend python -c "from app.core.config import Settings; Settings()"; echo EXIT_CODE=$?
```
**Evidence**
```
Traceback (most recent call last):
  File "/app/app/core/config.py", line 129, in _forbid_insecure_defaults_outside_dev
    raise InsecureConfigurationError(
app.core.exceptions.InsecureConfigurationError: Refusing to start with app_env='production' while using insecure default secret(s): JWT_SECRET. ...
EXIT_CODE=1
```
**Verdict:** Defense held on the REAL boot path. Env-driven `Settings()` raises and the process exits non-zero. Critically,
the injected `APP_ENV=production` **overrode** the container's `.env app_env=local` with **no** fail-open fallback to the
dev exemption — the gate keys off the effective, env-overridden `app_env`. This is the production-realistic deployment
path, not just a kwarg unit test.
