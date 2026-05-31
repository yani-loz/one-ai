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


# ======================= TC-IA-046 test logic =======================
# Demoted admin keeps admin power on a STILL-VALID access token until expiry, because
# require_company_admin reads role from the stateless JWT and never re-checks the DB
# (documented "access-token denylist" gap). Two admins so the demotion does not trip the
# last-admin guard.

async def main() -> None:
    sfx = stamp()
    slug = f"token-{sfx}"
    admin1_email = f"admin1-{sfx}@token.example.com"
    admin2_email = f"admin2-{sfx}@token.example.com"
    throwaway_email = f"throwaway-{sfx}@token.example.com"
    print(f"[setup] namespace=token-{sfx} slug={slug} admin1={admin1_email} admin2={admin2_email}")

    async with _client() as c:
        plat = await platform_login(c)
        ob = await onboard_org(c, plat, name=f"Token Org {sfx}", slug=slug, admin_email=admin1_email)
        print(f"[setup] onboard_org -> {ob.status_code}")
        assert ob.status_code == 201, ob.text
        admin1_id = ob.json()["admin"]["id"]
        print(f"[setup] admin1_id={admin1_id}")

        l1 = await login(c, admin1_email, DEFAULT_PW)
        assert l1.status_code == 200, l1.text
        at1 = l1.json()["access_token"]  # admin1's PRE-demotion access token
        print(f"[step1] admin1 login -> 200  AT1 captured (pre-demotion)")

        c2 = await create_user(c, at1, email=admin2_email, full_name="Second Admin", role="company_admin")
        print(f"[step2] admin1 creates admin2 (company_admin) -> {c2.status_code}  role={c2.json().get('role')}")
        assert c2.status_code == 201, c2.text

        l2 = await login(c, admin2_email, DEFAULT_PW)
        assert l2.status_code == 200, l2.text
        at2 = l2.json()["access_token"]
        print(f"[step2] admin2 login -> 200  AT2 captured")

        ctl = await c.get("/users", headers=bearer(at1))
        print(f"[step3] CONTROL admin1 GET /users with AT1 -> {ctl.status_code}  count={len(ctl.json()) if ctl.status_code==200 else ctl.text}")

        demote = await c.patch(f"/users/{admin1_id}", headers=bearer(at2), json={"role": "member"})
        print(f"[step4] admin2 PATCH admin1 -> member -> {demote.status_code}  new_role={demote.json().get('role') if demote.status_code==200 else demote.text}")

        # Re-login as admin1 to PROVE the DB really demoted them (fresh token carries member role)
        l1b = await login(c, admin1_email, DEFAULT_PW)
        fresh_role_claim = None
        if l1b.status_code == 200:
            fresh_at = l1b.json()["access_token"]
            fresh_role_claim = jwt.decode(fresh_at, DEV_SECRET, algorithms=["HS256"], audience=COMPANY_AUD).get("role")
        print(f"[step4b] admin1 re-login fresh token role claim = {fresh_role_claim!r}  (proves DB now says member)")

        get_after = await c.get("/users", headers=bearer(at1))
        print(f"[step5] admin1 GET /users with STALE pre-demotion AT1 -> {get_after.status_code}  count={len(get_after.json()) if get_after.status_code==200 else get_after.text}")

        post_after = await create_user(c, at1, email=throwaway_email, full_name="Throwaway", role="member")
        print(f"[step6] admin1 POST /users with STALE AT1 -> {post_after.status_code}  created_role={post_after.json().get('role') if post_after.status_code==201 else post_after.text}")

        # "Contract-as-deferred" outcome: stale token STILL works (gap is real & live)
        gap_live = (
            demote.status_code == 200
            and demote.json().get("role") == "member"
            and fresh_role_claim == "member"
            and get_after.status_code == 200
            and post_after.status_code == 201
        )
        print(f"[verdict] documented-denylist-gap LIVE={gap_live} (demoted in DB, yet stale AT1 still GETs 200 & POSTs 201)")


asyncio.run(main())
