"""
Shared harness helpers for the Infrastructure + AuthN/AuthZ target.

HOW TO USE (scripts run INSIDE the backend container via stdin, which has no
filesystem imports): each per-case harness script INLINES this file's contents at
the top, then appends its own `asyncio.run(main())` test logic, and is executed with:

    docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/<script>.py

Everything talks to the REAL running uvicorn at http://localhost:8000 (not the
in-process ASGI app) so transaction/connection-pool behaviour — where the races live —
is exercised for real.

Isolation rule: the DB is persistent and shared. Every run provisions FRESH,
run-stamped orgs and unique emails via `stamp()` so parallel harnesses never collide,
and NEVER mutates the demo org's admin.
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

# Demo platform admin (dev seed) — used ONLY to onboard fresh test orgs.
PLATFORM_EMAIL = "super@ethera.ai"
PLATFORM_PW = "Sup3r-Dev-Only-2026!"

DEFAULT_PW = "Valid-Pass-2026!"  # 16 bytes, satisfies BcryptPassword (8..128 chars, <=72 bytes)


def stamp() -> str:
    """Short lowercase-alnum run id (slug-safe) so each run's data is unique."""
    return f"{int(time.time() * 1000):x}{uuid4().hex[:4]}"


def bearer(token: str) -> dict[str, str]:
    """Authorization header for a bearer token."""
    return {"Authorization": f"Bearer {token}"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE, timeout=30)


# — Auth flows against the live server —

async def platform_login(c: httpx.AsyncClient) -> str:
    """Log in the demo platform admin; return its access token."""
    r = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
    r.raise_for_status()
    return r.json()["access_token"]


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
    """Onboard a new org + its first company_admin. Returns the raw response."""
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


async def login(c: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    """Company-user login. Returns the raw response (caller inspects status/body)."""
    return await c.post("/auth/login", json={"email": email, "password": password})


async def create_user(
    c: httpx.AsyncClient,
    admin_token: str,
    *,
    email: str,
    full_name: str,
    role: str,
    password: str = DEFAULT_PW,
) -> httpx.Response:
    """company_admin creates a user in their own org. Returns the raw response."""
    return await c.post(
        "/users",
        headers=bearer(admin_token),
        json={"email": email, "full_name": full_name, "role": role, "password": password},
    )


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
    """Mint a company access token. Knobs let callers craft hostile variants
    (wrong secret, alg=none, missing claims via `drop`, expired)."""
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


def forge_platform_token(*, sub: str | None = None, ttl_min: int = 15, secret: str = DEV_SECRET) -> str:
    """Mint a platform access token (aud='platform')."""
    return forge_company_token(
        sub=sub or str(uuid4()), org_id=None, role="platform_admin", ttl_min=ttl_min,
        secret=secret, aud=PLATFORM_AUD,
    )


def sha256_hex(raw: str) -> str:
    """sha256 hex of a raw refresh token (matches the server's storage form)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# — Concurrency —

async def fire_concurrent(make_coro, n: int) -> list:
    """Run n coroutines concurrently; return results (exceptions captured, not raised)."""
    return await asyncio.gather(*(make_coro(i) for i in range(n)), return_exceptions=True)


def summarize(results: list) -> dict[int, int]:
    """Tally HTTP status codes (or 'EXC') across a list of responses/exceptions."""
    tally: dict = {}
    for r in results:
        key = "EXC" if isinstance(r, BaseException) else r.status_code
        tally[key] = tally.get(key, 0) + 1
    return tally



# ============================ TC-IA-029 test logic ============================
# Tokens that pass signature+aud but carry non-UUID sub OR non-UUID org_id must be
# 401 TokenInvalid, NOT 500: _principal_from_claims wraps UUID(...) in try/except
# (dependencies.py:60-66) -> TokenInvalidError. A 500 + traceback would be the finding.
# Each forged token isolates ONE bad field (the other stays valid).

async def main() -> None:
    async with _client() as c:
        valid_uuid = str(uuid4())

        bad_sub = forge_company_token(
            sub="not-a-uuid", org_id=valid_uuid, role="company_admin"
        )
        a_me = await c.get("/auth/me", headers=bearer(bad_sub))
        print(f"[attack] GET /auth/me (sub='not-a-uuid') -> {a_me.status_code} {a_me.text}")
        a_u = await c.get("/users", headers=bearer(bad_sub))
        print(f"[attack] GET /users (sub='not-a-uuid') -> {a_u.status_code} {a_u.text}")

        bad_org = forge_company_token(
            sub=valid_uuid, org_id="not-a-uuid", role="company_admin"
        )
        b_me = await c.get("/auth/me", headers=bearer(bad_org))
        print(f"[attack] GET /auth/me (org_id='not-a-uuid') -> {b_me.status_code} {b_me.text}")
        b_u = await c.get("/users", headers=bearer(bad_org))
        print(f"[attack] GET /users (org_id='not-a-uuid') -> {b_u.status_code} {b_u.text}")


asyncio.run(main())
