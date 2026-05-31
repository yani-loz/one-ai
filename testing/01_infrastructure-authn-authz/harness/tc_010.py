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


# ======================== TC-IA-010 test logic ========================

SECRET_FIELD_HINTS = ("password", "hash", "secret", "salt")


def scan_for_secrets(obj, path="") -> list[str]:
    """Recursively collect any dict keys that look like a credential/hash leak."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{path}.{k}" if path else k
            if any(h in k.lower() for h in SECRET_FIELD_HINTS):
                hits.append(kp)
            hits.extend(scan_for_secrets(v, kp))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(scan_for_secrets(v, f"{path}[{i}]"))
    return hits


async def main() -> None:
    s = stamp()
    slug = f"authn010-{s}"
    org_name = f"authn010 co-{s}"
    admin_email = f"admin-authn010-{s}@oneai.dev"

    async with _client() as c:
        plat = await platform_login(c)
        ob = await onboard_org(
            c, plat, name=org_name, slug=slug, admin_email=admin_email,
            admin_name="Authn010 Admin",
        )
        ob_json = ob.json()
        org_id = ob_json.get("organization", {}).get("id")
        print(f"== onboard == {ob.status_code} org_id={org_id} org_name={org_name} {slug}")

        r = await login(c, admin_email, DEFAULT_PW)
        body = r.json()
        print(f"== POST /auth/login == {r.status_code}")
        print(f"body keys: {sorted(body.keys())}")

        at = body.get("access_token")
        rt = body.get("refresh_token")
        print(f"access_token present: {bool(at)} (len={len(at) if at else 0})  "
              f"refresh_token present: {bool(rt)} (len={len(rt) if rt else 0})")
        print(f"tokens distinct: {at != rt}")
        print(f"token_type: {body.get('token_type')}")

        user = body.get("user") or {}
        print(f"user keys: {sorted(user.keys())}")
        print(f"user.email: {user.get('email')}")
        print(f"user.role: {user.get('role')}")
        print(f"user.org_name: {user.get('org_name')}")

        leaks = scan_for_secrets(body)
        print(f"secret-field scan (password/hash) in body: "
              f"{leaks if leaks else 'NONE FOUND'}")

        expected_keys = {"id", "email", "full_name", "role", "org_id", "org_name"}
        ok = (
            r.status_code == 200
            and bool(at) and bool(rt) and at != rt
            and body.get("token_type") == "bearer"
            and set(user.keys()) == expected_keys
            and user.get("email") == admin_email
            and user.get("role") == "company_admin"
            and user.get("org_name") == org_name
            and not leaks
        )
        print(f"RESULT: {'PASS' if ok else 'FAIL'}")


asyncio.run(main())
