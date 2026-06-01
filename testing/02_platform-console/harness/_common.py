"""
Shared harness helpers for the Platform Console target (/platform/* + reused onboarding).

HOW TO USE (scripts run INSIDE the backend container via stdin, which has no
filesystem imports): each per-case harness script INLINES this file's contents at the
top, then appends its own `async def main(): ...` + `asyncio.run(main())`, and is run with:

    docker compose exec -T backend python - < testing/02_platform-console/harness/<script>.py

Everything talks to the REAL running uvicorn at http://localhost:8000 (not the in-process
ASGI app) so transaction/connection-pool behaviour — where the races and pool-exhaustion
live — is exercised for real.

Isolation rule: the DB is persistent and shared. Every run provisions FRESH, run-stamped
orgs and unique emails via `stamp()` so parallel harnesses never collide, and NEVER mutates
the demo platform admin (`super@ethera.ai`) — stranding it breaks the dev login panel with
no in-app recovery.

Key facts this harness encodes (One AI identity module):
  - Tenant key is `org_id`; RLS is DEFINED BUT INERT (app connects as superuser → bypass),
    so the app-layer org_id filter + the JWT secret are the only active controls.
  - The dev JWT secret is the forgeable default (DEV_SECRET) — forged tokens are a real
    capability and prove isolation rests entirely on JWT secrecy.
  - Two auth domains split by JWT audience: aud='company' (users) vs aud='platform' (Ethera
    staff). Refresh rotation is single-use and scoped by subject_type.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt

BASE = "http://localhost:8000"
DEV_SECRET = "dev-only-insecure-secret-change-me-in-prod"  # the forgeable dev default
ALG = "HS256"
COMPANY_AUD = "company"
PLATFORM_AUD = "platform"

# Demo platform admin (dev seed) — used ONLY to onboard fresh test orgs. Never mutate it.
PLATFORM_EMAIL = "super@ethera.ai"
PLATFORM_PW = "Sup3r-Dev-Only-2026!"

DEFAULT_PW = "Valid-Pass-2026!"  # 16 bytes, satisfies BcryptPassword (8..128 chars, <=72 bytes)


def stamp() -> str:
    """Short lowercase-alnum run id (slug-safe) so each run's data is unique."""
    return f"{int(time.time() * 1000):x}{uuid4().hex[:4]}"


def bearer(token: str) -> dict[str, str]:
    """Authorization header for a bearer token."""
    return {"Authorization": f"Bearer {token}"}


def _client(timeout: float = 30) -> httpx.AsyncClient:
    """An async client bound to the live server. Bump `timeout` above the server's
    pool_timeout (30s default) for stress cases, so you measure the SERVER not the client."""
    return httpx.AsyncClient(base_url=BASE, timeout=timeout)


def sha256_hex(raw: str) -> str:
    """sha256 hex of a raw refresh token (matches the server's storage form)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# — Platform auth flows against the live server —

async def platform_login(c: httpx.AsyncClient) -> str:
    """Log in the demo platform admin; return its access token only."""
    access, _refresh = await platform_login_pair(c)
    return access


async def platform_login_pair(c: httpx.AsyncClient) -> tuple[str, str]:
    """Log in the demo platform admin; return (access_token, refresh_token).

    Use the access token to onboard/list/me; use the refresh token for the rotation,
    logout, and reuse cases. /platform/login excludes the null `user` field by design.
    """
    r = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["refresh_token"]


async def onboard_org(
    c: httpx.AsyncClient,
    plat_token: str,
    *,
    name: str,
    slug: str,
    admin_email: str,
    admin_name: str = "Org Admin",
    admin_pw: str = DEFAULT_PW,
) -> httpx.Response:
    """Onboard a new org + its first company_admin via POST /platform/orgs. Raw response."""
    return await c.post(
        "/platform/orgs",
        headers=bearer(plat_token),
        json={
            "org_name": name,
            "org_slug": slug,
            "admin_email": admin_email,
            "admin_full_name": admin_name,
            "admin_password": admin_pw,
        },
    )


async def provision_company(
    c: httpx.AsyncClient, plat_token: str, prefix: str
) -> dict[str, str]:
    """Onboard one fresh run-stamped org + admin, then log the admin in.

    Returns a dict with: org_id, slug, admin_email, admin_access, admin_refresh — a complete
    company identity for cross-domain tests (e.g. a REAL company refresh token to present at
    /platform/refresh). Raises on any non-2xx so callers fail loud, not silent.
    """
    slug = f"{prefix}-{stamp()}"
    admin_email = f"admin-{slug}@oneai.dev"
    onboarded = await onboard_org(
        c, plat_token, name=f"Org {slug}", slug=slug, admin_email=admin_email
    )
    onboarded.raise_for_status()
    org_id = onboarded.json()["organization"]["id"]
    access, refresh = await company_login_pair(c, admin_email, DEFAULT_PW)
    return {
        "org_id": org_id,
        "slug": slug,
        "admin_email": admin_email,
        "admin_access": access,
        "admin_refresh": refresh,
    }


async def login(c: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    """Company-user login (/auth/login). Returns the raw response (inspect status/body)."""
    return await c.post("/auth/login", json={"email": email, "password": password})


async def company_login_pair(
    c: httpx.AsyncClient, email: str, password: str = DEFAULT_PW
) -> tuple[str, str]:
    """Log a company user in; return (access_token, refresh_token). Raises on non-2xx."""
    r = await login(c, email, password)
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["refresh_token"]


# — Token forging (proves isolation rests entirely on JWT secrecy; RLS is inert) —

def forge_company_token(
    *,
    sub: str,
    org_id: str | None,
    role: str = "company_admin",
    ttl_min: int = 15,
    secret: str = DEV_SECRET,
    aud: str = COMPANY_AUD,
    alg: str = ALG,
    drop: tuple[str, ...] = (),
    expired: bool = False,
) -> str:
    """Mint a company access token. Knobs craft hostile variants (wrong secret, alg=none,
    missing claims via `drop`, expired). For the DISCRIMINATING cross-domain test, forge a
    company-aud token whose `sub` is a REAL platform admin id (so the audience guard is the
    only thing preventing a 200 at /platform/me)."""
    now = datetime.now(UTC)
    iat = now - timedelta(minutes=ttl_min + 5) if expired else now
    exp = iat + timedelta(minutes=ttl_min)
    claims: dict[str, object] = {
        "sub": sub,
        "type": "access",
        "aud": aud,
        "role": role,
        "org_id": org_id,
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": str(uuid4()),
    }
    for k in drop:
        claims.pop(k, None)
    if alg == "none":
        return jwt.encode(claims, key=None, algorithm="none")
    return jwt.encode(claims, secret, algorithm=alg)


def forge_platform_token(
    *, sub: str | None = None, ttl_min: int = 15, secret: str = DEV_SECRET,
    alg: str = ALG, drop: tuple[str, ...] = (), expired: bool = False,
) -> str:
    """Mint a platform access token (aud='platform'). With sub=<random uuid> this exercises
    the unknown-admin /platform/me path (401) WITHOUT touching the real demo admin."""
    return forge_company_token(
        sub=sub or str(uuid4()), org_id=None, role="platform_admin", ttl_min=ttl_min,
        secret=secret, aud=PLATFORM_AUD, alg=alg, drop=drop, expired=expired,
    )


# — Concurrency / stress —

async def fire_concurrent(make_coro, n: int) -> list:
    """Run n coroutines concurrently; return results (exceptions captured, not raised)."""
    return await asyncio.gather(*(make_coro(i) for i in range(n)), return_exceptions=True)


def summarize(results: list) -> dict:
    """Tally HTTP status codes across responses; bucket exceptions by type name.

    Keeps a SERVER 500 distinct from a CLIENT-side failure (a pool/connect timeout shows as
    e.g. 'EXC:PoolTimeout' / 'EXC:ConnectTimeout', not as a status code) — the distinction
    that makes a stress verdict meaningful.
    """
    tally: dict = {}
    for r in results:
        if isinstance(r, BaseException):
            key = f"EXC:{type(r).__name__}"
        else:
            key = r.status_code
        tally[key] = tally.get(key, 0) + 1
    return tally
