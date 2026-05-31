"""
Shared harness helpers — STARTER. Copy to testing/<NN>_<target>/harness/_common.py and
adapt the CONFIG block + the TARGET-SPECIFIC helpers to the module under test.

HOW SCRIPTS RUN (no filesystem imports — they run over stdin inside the backend container):
each per-case script INLINES this file's contents at the top, appends its own
`async def main(): ...` + `asyncio.run(main())`, and is executed with:

    docker compose exec -T backend python - < testing/<NN>_<target>/harness/<script>.py

Everything talks to the REAL running uvicorn at http://localhost:8000 (not the in-process
ASGI app) so transaction/connection-pool behaviour — where the races live — is exercised.

Isolation rule: the DB is persistent and shared. Every run provisions FRESH, run-stamped
orgs and unique emails via `stamp()` so parallel harnesses never collide, and NEVER
mutates the demo org's admin.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt

# ─────────────────────────── CONFIG — adapt per environment ───────────────────────────
BASE = "http://localhost:8000"
DEV_SECRET = "dev-only-insecure-secret-change-me-in-prod"  # the forgeable dev default
ALG = "HS256"
COMPANY_AUD = "company"
PLATFORM_AUD = "platform"
PLATFORM_EMAIL = "super@ethera.ai"          # demo platform admin (dev seed)
PLATFORM_PW = "Sup3r-Dev-Only-2026!"
DEFAULT_PW = "Valid-Pass-2026!"             # 16 bytes — satisfies an 8..72-byte password rule
# ───────────────────────────────────────────────────────────────────────────────────────


def stamp() -> str:
    """Short lowercase-alnum run id (slug-safe) so each run's data is unique."""
    return f"{int(time.time() * 1000):x}{uuid4().hex[:4]}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE, timeout=30)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Concurrency ──
async def fire_concurrent(make_coro, n: int) -> list:
    """Run n coroutines concurrently; return results (exceptions captured, not raised)."""
    return await asyncio.gather(*(make_coro(i) for i in range(n)), return_exceptions=True)


def summarize(results: list) -> dict:
    """Tally HTTP status codes (or 'EXC') across responses/exceptions."""
    tally: dict = {}
    for r in results:
        key = "EXC" if isinstance(r, BaseException) else r.status_code
        tally[key] = tally.get(key, 0) + 1
    return tally


# ── Token forging (proves isolation rests on JWT secrecy when RLS is inert) ──
def forge_company_token(
    *, sub: str, org_id: str | None, role: str = "company_admin", ttl_min: int = 15,
    secret: str = DEV_SECRET, aud: str = COMPANY_AUD, alg: str = ALG,
    drop: tuple[str, ...] = (), expired: bool = False,
) -> str:
    """Mint a company access token. Knobs craft hostile variants (wrong secret, alg=none,
    missing claims via `drop`, expired)."""
    now = datetime.now(UTC)
    iat = now - timedelta(minutes=ttl_min + 5) if expired else now
    exp = iat + timedelta(minutes=ttl_min)
    claims: dict = {
        "sub": sub, "type": "access", "aud": aud, "role": role, "org_id": org_id,
        "iat": int(iat.timestamp()), "exp": int(exp.timestamp()), "jti": str(uuid4()),
    }
    for k in drop:
        claims.pop(k, None)
    if alg == "none":
        return jwt.encode(claims, key=None, algorithm="none")
    return jwt.encode(claims, secret, algorithm=alg)


def forge_platform_token(*, sub: str | None = None, ttl_min: int = 15, secret: str = DEV_SECRET) -> str:
    return forge_company_token(sub=sub or str(uuid4()), org_id=None, role="platform_admin",
                               ttl_min=ttl_min, secret=secret, aud=PLATFORM_AUD)


# ═══════════════ TARGET-SPECIFIC — adapt these to the module under test ═══════════════

async def platform_login(c: httpx.AsyncClient) -> str:
    """Log in the demo platform admin; return its access token."""
    r = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
    r.raise_for_status()
    return r.json()["access_token"]


async def onboard_org(c, plat_token, *, name, slug, admin_email,
                      admin_name="Org Admin", admin_pw=DEFAULT_PW) -> httpx.Response:
    """Onboard a new org + its first company_admin. Returns the raw response."""
    return await c.post("/platform/orgs", headers=bearer(plat_token), json={
        "org_name": name, "org_slug": slug, "admin_email": admin_email,
        "admin_full_name": admin_name, "admin_password": admin_pw,
    })


async def login(c, email, password) -> httpx.Response:
    """Company-user login. Returns the raw response (caller inspects status/body)."""
    return await c.post("/auth/login", json={"email": email, "password": password})


async def create_user(c, admin_token, *, email, full_name, role, password=DEFAULT_PW) -> httpx.Response:
    """company_admin creates a user in their own org. Returns the raw response."""
    return await c.post("/users", headers=bearer(admin_token), json={
        "email": email, "full_name": full_name, "role": role, "password": password,
    })
