"""
Role: Shared reproducible primitives for Target 09 (SG) — the secrets-gate + RLS-invariant
      adversarial suite. Pure helpers + documented shell recipes; each TC-SG-*.md carries its
      own command + raw evidence inline (this module is the common block they share).
Used by: the TC-SG-*.md case files (as the inlined COMMON block when piped into the backend
         container over stdin) and any re-run of Target 09.
Depends on: app.core.config.Settings (in-container), PyJWT, hashlib/hmac (stdlib). No project writes.
Key invariants:
  - Boot-gate checks run in SEPARATE one-shot `docker compose exec` processes — never the live server.
  - Every DDL recipe targets a uniquely-named THROWAWAY scratch DB (oneai_sg_*), dropped after.
  - Read-only on the real `oneai` DB; firing cases inject the insecure value EXPLICITLY (no spurious
    fail-open). See docs/audits/2026-06-02_secrets-gate-rls-invariant-dynamic-adversarial.md.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# — Ground truth (the live dev container; reproduced in evidence, never assumed) —
BASE = "http://localhost:8000"
DEV_SECRET = "dev-only-insecure-secret-change-me-in-prod"   # forgeable; live in app_env=local
DEV_PG_PASSWORD = "oneai"
STRONG_SECRET = "a-strong-random-secret-32-bytes!!"          # stand-in real secret for isolating one leg
STRONG_PG = "a-strong-real-pw"
EXEMPT_ENVS = ("local", "test")

# — Two demo orgs that both carry users (read-only cross-org proof; IDs may differ per seed run) —
# Discover live with:  SELECT org_id, count(*) FROM users GROUP BY org_id ORDER BY 2 DESC;
ORG_A_GLOBEX = "1f9e5e89-eb9d-4f07-940e-bb618e23c63d"
ORG_B_DEMO = "bd94a689-c81e-4926-8c4e-faeb9ac9e06d"


def build_settings(**overrides: object) -> tuple[bool, str]:
    """Construct Settings with the given overrides; return (booted, message).

    Contract: imports inside the call so this runs when inlined into a `docker compose exec
    -T backend python -` stdin script. `booted` is False when the fail-closed gate raises
    InsecureConfigurationError; `message` carries the exception text (which names the offending
    secret) or "BOOTED". Pass insecure values EXPLICITLY — never rely on the field default.
    """
    from app.core.config import Settings  # noqa: PLC0415 — deferred for stdin-inlining
    from app.core.exceptions import InsecureConfigurationError  # noqa: PLC0415

    try:
        settings = Settings(**overrides)  # type: ignore[arg-type]
    except InsecureConfigurationError as exc:
        return False, str(exc)
    return True, f"BOOTED requires_secure_secrets={settings.requires_secure_secrets}"


def forge_platform_token(secret: str, sub: str, ttl_seconds: int = 3600) -> str:
    """Forge a platform-audience access token signed with `secret` (HS256).

    Mirrors the app's signing path (identity/security/tokens.py signs HS256 with settings.jwt_secret).
    A token signed with the live DEV_SECRET is accepted because the dev stack is forgeable — that is
    the point of TC-SG-021. `sub` need not be a real admin row for the metadata read.
    """
    import jwt  # noqa: PLC0415 — deferred for stdin-inlining

    now = int(time.time())
    claims = {
        "sub": sub,
        "type": "access",
        "aud": "platform",
        "role": "platform_admin",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def hmac_sha256_roundtrip(secret: str) -> bool:
    """Prove an HS256 MAC made with `secret` verifies with the same `secret` (the forgery core).

    Used by TC-SG-006 to show the empty/blank-key HMAC class is mathematically valid even when
    PyJWT guards the literal '' at the encode boundary.
    """
    key = secret.encode()
    mac = hmac.new(key, b"forged.payload", hashlib.sha256).digest()
    return hmac.compare_digest(mac, hmac.new(key, b"forged.payload", hashlib.sha256).digest())


# ───────────────────────── Shell recipes (psql / scratch-DB) ─────────────────────────
# Boot-gate (Suite A), in-container, one-shot — env-var path (TC-SG-002):
#   docker compose exec -T -e APP_ENV=production -e JWT_SECRET=dev-only-insecure-secret-change-me-in-prod \
#       -e POSTGRES_PASSWORD=a-strong-real-pw backend python -c \
#       "from app.core.config import Settings; Settings()"; echo EXIT_CODE=$?   # → exit 1 + InsecureConfigurationError
#
# Live catalog truth (Suite B, TC-SG-011) — READ-ONLY on real oneai:
#   docker compose exec -T db psql -U oneai -d oneai -c \
#     "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class \
#      WHERE relname IN ('users','support_grant') AND relnamespace='public'::regnamespace ORDER BY relname;"
#   docker compose exec -T db psql -U oneai -d oneai -c \
#     "SELECT tablename, policyname, qual, with_check FROM pg_policies \
#      WHERE tablename IN ('users','support_grant') ORDER BY tablename;"
#
# Faithful teeth proof (Suite B, TC-SG-013/015) — THROWAWAY scratch DB, trap-dropped:
#   SDB=oneai_sg_teeth_$(date +%s)
#   trap 'docker compose exec -T db psql -U oneai -d oneai -c "DROP DATABASE IF EXISTS '"$SDB"'"' EXIT
#   docker compose exec -T db psql -U oneai -d oneai -c "CREATE DATABASE $SDB"
#   docker compose exec -T -e POSTGRES_DB=$SDB backend alembic upgrade head
#   docker compose exec -T db psql -U oneai -d $SDB -c "DROP POLICY org_isolation ON support_grant"  # non-sentinel → RED
#   docker compose exec -T -e POSTGRES_DB=$SDB backend python -m pytest \
#       tests/identity/models/test_rls_invariants.py --no-cov -rs -v        # → 1 failed (missing policy)
#   # (drop on `users` instead → the test SKIPs, not FAILs — the sentinel blind spot, TC-SG-015)
#
# Superuser RLS-bypass proof (Suite C, TC-SG-020) — READ-ONLY, ROLLBACK:
#   docker compose exec -T db psql -U oneai -d oneai -c \
#     "BEGIN; SELECT set_config('app.current_org_id','<orgA>',true); \
#      SELECT count(*) FROM users WHERE org_id='<orgB>'; ROLLBACK;"        # → >0 (policy bypassed)
