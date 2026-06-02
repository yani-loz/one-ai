# TC-SG-005: Exempt envs preserved — local / test / LOCAL boot with dev secrets (dev convenience intact)

| Field | Value |
|---|---|
| **ID** | TC-SG-005 · **Suite** A · **Type** Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** over-tightening could break dev/CI — the broadened gate might start rejecting the legitimately-exempt
`local`/`test` envs (or be case-sensitive and reject `LOCAL`), blocking the dockerized dev stack and the test suite.

**Command**
```
docker compose exec -T backend python - <<'PY'
for env in ['local', 'test', 'LOCAL']:
    Settings(app_env=env, jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='oneai')
PY
```
**Evidence**
```
[005-'local'] BOOTED (no raise) requires_secure_secrets=False
[005-'test']  BOOTED (no raise) requires_secure_secrets=False
[005-'LOCAL'] BOOTED (no raise) requires_secure_secrets=False
```
**Verdict:** Defense held in the negative direction. Both exempt envs boot with dev defaults, and the exemption is
case-insensitive (`LOCAL` boots) — matching the live container (`app_env='local'`, forgeable dev secret,
`requires_secure_secrets=False`, ground truth reproduced). Dev convenience preserved without weakening the non-dev gate.
