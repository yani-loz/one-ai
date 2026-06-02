# TC-SG-006: NEW — blank prod secret bypasses the exact-match denylist; ' ' yields forgeable production tokens

| Field | Value |
|---|---|
| **ID** | TC-SG-006 · **Suite** A · **Type** Adversarial · **Severity if fail** Low |
| **Result** | ⚠️ Pass-with-concern · **Tag** 🆕 NEW (Low) · **Status** Executed |

## Execution result (2026-06-02)
**Break hypothesis:** the gate denylists only the two exact known dev-default strings (`config.py:120-127`, `value == default`).
An empty (`''`) or blank (`' '`) `jwt_secret` is `!= default`, so it slips the gate and BOOTs in production — leaving a
publicly-guessable signing key.

**Command**
```
docker compose exec -T backend python - <<'PY'
Settings(app_env='production', jwt_secret='',  postgres_password='a-strong-real-pw')   # 006a
Settings(app_env='production', jwt_secret=' ', postgres_password='a-strong-real-pw')   # 006b
import jwt; t = jwt.encode({'sub':'attacker','org_id':'victim-org'}, ' ', algorithm='HS256'); jwt.decode(t, ' ', algorithms=['HS256'])
PY
```
**Evidence**
```
[006a-prod-EMPTY-jwt]  BOOTED (no raise). jwt_secret='' requires_secure_secrets=True
[006b-prod-SPACE-jwt]  BOOTED (no raise). jwt_secret=' ' requires_secure_secrets=True
--- forgery with the booted ' ' secret ---
InsecureKeyLengthWarning: HMAC key is 1 byte (< 32 recommended)   # PyJWT WARNS, does NOT block
forged token decodes: {'sub':'attacker','org_id':'victim-org'}; attacker re-signs with guessed ' ' → verify=True
--- literal '' wrinkle (documented honestly) ---
jwt.encode(payload, '', 'HS256') → InvalidKeyError: HMAC key must not be empty   # crash-at-issuance, NOT forgery
raw HMAC-SHA256 with EMPTY key round-trips (verify=True): True                   # the class is mathematically valid
```
**Verdict:** Concern, not a contract violation. Both blank secrets BOOT in production; the `' '` variant then signs a
production token an attacker forges by guessing `' '` (the path is real — `identity/security/tokens.py:60` signs HS256
with `settings.jwt_secret`). Honest wrinkle: literal `''` degrades to a **crash-at-issuance** (PyJWT refuses an empty key)
rather than silent forgery; `' '` is the realistic forgeable bypass. The gate's LITERAL contract (exact-match on the two
known dev defaults) is narrower than its PURPOSE (no forgeable prod key) — BEYOND the gate's stated scope, a hardening
opportunity. **NEW:** no empty/blank case in `test_config.py`; `FIX_BEFORE_PROD.md:46` covers only rotating the *known
default*. **Severity Low:** requires an operator to deploy a blank secret (misconfiguration), and `''` degrades to a crash.
**Remediation:** add a non-empty + minimum-length (≥32 byte) check when `requires_secure_secrets`. Independently
reproduced by the verify agent.

## Remediation landed (2026-06-02)
Fixed in `backend/app/core/config.py` — `_forbid_insecure_defaults_outside_dev` now also rejects any non-dev
`JWT_SECRET` that is blank, whitespace-only, or under `_MIN_JWT_SECRET_BYTES = 32` (RFC 7518 §3.2 HS256 floor).
Covered by 5 new `test_config.py` cases (blank / whitespace / 31-byte → raise; 32-byte → boots; `local` short → boots)
and re-verified live against the running image:
```
[empty '']     RAISED → JWT_SECRET (blank or under 32 bytes)
[space ' ']    RAISED → JWT_SECRET (blank or under 32 bytes)
[short x*31]   RAISED → JWT_SECRET (blank or under 32 bytes)
[ok x*32]      BOOTED
[dev-default]  RAISED → JWT_SECRET (dev default)
```
Full backend suite green (197 passed, 92.67% cov). No strength floor on `POSTGRES_PASSWORD` (blank can be legitimate
IAM/peer/cert auth). The bypass class is closed; the residual is operational secret rotation.

