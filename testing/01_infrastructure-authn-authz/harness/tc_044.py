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


# ======================= TC-IA-044 test logic =======================
# Deactivated user's still-unexpired ACCESS token on /auth/me -> 401
# (build_authenticated_user_by_id re-checks is_active). Target is a MEMBER so the
# last-admin guard does not interfere.

async def main() -> None:
    sfx = stamp()
    slug = f"token-{sfx}"
    admin_email = f"admin-{sfx}@token.example.com"
    victim_email = f"victim-{sfx}@token.example.com"
    print(f"[setup] namespace=token-{sfx} slug={slug} admin={admin_email} victim={victim_email}")

    async with _client() as c:
        plat = await platform_login(c)
        ob = await onboard_org(c, plat, name=f"Token Org {sfx}", slug=slug, admin_email=admin_email)
        print(f"[setup] onboard_org -> {ob.status_code}")
        assert ob.status_code == 201, ob.text

        al = await login(c, admin_email, DEFAULT_PW)
        assert al.status_code == 200, al.text
        admin_at = al.json()["access_token"]

        cu = await create_user(c, admin_at, email=victim_email, full_name="Victim Member", role="member")
        print(f"[step1] create member -> {cu.status_code}  is_active={cu.json().get('is_active')}")
        assert cu.status_code == 201, cu.text
        member_id = cu.json()["id"]

        vl = await login(c, victim_email, DEFAULT_PW)
        assert vl.status_code == 200, vl.text
        victim_at = vl.json()["access_token"]
        print(f"[step2] victim login -> 200  access token captured")

        me_before = await c.get("/auth/me", headers=bearer(victim_at))
        print(f"[step3] /auth/me (active) -> {me_before.status_code}  email={me_before.json().get('email') if me_before.status_code==200 else me_before.text}")

        dl = await c.delete(f"/users/{member_id}", headers=bearer(admin_at))
        print(f"[step4] admin DELETE /users/{{member}} -> {dl.status_code}  body={dl.text!r}")

        me_after = await c.get("/auth/me", headers=bearer(victim_at))
        print(f"[step5] /auth/me (SAME unexpired token, now deactivated) -> {me_after.status_code}  body={me_after.text}")

        held = (
            cu.status_code == 201
            and me_before.status_code == 200
            and dl.status_code == 204
            and me_after.status_code == 401
        )
        print(f"[verdict] deactivated-access-on-me HELD={held} (before==200, delete==204, after==401)")


asyncio.run(main())
