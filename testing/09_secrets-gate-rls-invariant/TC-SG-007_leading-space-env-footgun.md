# TC-SG-007: Whitespace foot-gun — ' local' (leading space) RAISES (safe/fail-closed, but an operator usability trap)

| Field | Value |
|---|---|
| **ID** | TC-SG-007 · **Suite** A · **Type** Adversarial · **Severity if fail** Info |
| **Result** | ⚠️ Pass-with-concern · **Tag** — NA · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** if the gate stripped whitespace, `' local'` would be treated as exempt `local` and boot on dev
secrets — a fail-OPEN. Conversely, since it does not strip, an operator who pads `APP_ENV=' local'` gets an unexpected hard
boot failure — a fail-closed foot-gun.

**Command**
```
docker compose exec -T backend python - <<'PY'
Settings(app_env=' local', jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='oneai')   # padded
Settings(app_env='local',  jwt_secret='dev-only-insecure-secret-change-me-in-prod', postgres_password='oneai')   # control
PY
```
**Evidence**
```
[007-' local']        RAISED: Refusing to start with app_env=' local' while using insecure default secret(s): JWT_SECRET, POSTGRES_PASSWORD. ...
[007-control-'local'] BOOTED requires_secure_secrets=False
```
**Verdict:** Safe direction (fails CLOSED): `' local'` is not in `{local,test}`, so `requires_secure_secrets=True` and the
dev secrets trip the guard — exactly what we want (no whitespace-padding bypass of the exemption). The control proves it is
the leading space, not the value, that flips the result. **NA / Info:** not a security defect (it errs safe), but a
usability foot-gun — a padded env value yields a cryptic boot failure pointing at the secrets rather than the whitespace.
Optional ergonomics fix: `.strip()` `app_env`.
