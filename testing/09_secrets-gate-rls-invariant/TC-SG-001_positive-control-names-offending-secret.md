# TC-SG-001: Positive control — prod+dev-JWT names JWT_SECRET; inverse prod+default-pw names POSTGRES_PASSWORD

| Field | Value |
|---|---|
| **ID** | TC-SG-001 · **Suite** A · **Type** Positive · **Severity if fail** Critical |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Command**
```
docker compose exec -T backend python - <<'PY'   # two constructions, dev value injected EXPLICITLY
Settings(app_env='production', jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='a-strong-real-pw')
Settings(app_env='production', jwt_secret='a-strong-random-secret-32-bytes!!', postgres_password='oneai')
PY
```
**Evidence**
```
[001a-prod-devJWT]     RAISED InsecureConfigurationError: ... insecure default secret(s): JWT_SECRET. ...
[001b-prod-defaultPW]  RAISED InsecureConfigurationError: ... insecure default secret(s): POSTGRES_PASSWORD. ...
```
**Verdict:** Defense held. Both denylisted dev defaults are caught under `app_env=production` and the error names the
exact offending secret. Dev secret injected explicitly (SUITE-A trap respected — no spurious fail-open). Mirrors
`test_config.py` but reproduced live against the running image.
