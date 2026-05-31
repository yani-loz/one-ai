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


# ─────────────────────────────────────────────────────────────────────────────
# TC-IA-005 — CORS posture: credentialed wildcard methods/headers
#             (Adversarial, CONFIRMS-DOCUMENTED). Sev Low.
# main.py:40-46 wires CORSMiddleware with allow_origins=settings.cors_origins
# (env-restricted allowlist), allow_credentials=True, allow_methods=["*"],
# allow_headers=["*"]. FIX_BEFORE_PROD "Lock CORS" tracks the wildcard-with-
# credentials breadth.
# A real preflight needs BOTH Origin AND Access-Control-Request-Method.
# Probe 1 (evil origin): expect NO Access-Control-Allow-Origin echoing 'evil'
#   (allowlist holds — the origin is rejected / not reflected).
# Probe 2 (allowed origin http://localhost:5173): expect ACAO echoed,
#   Allow-Credentials: true, and wildcard-reflected methods/headers (the concern).
# A break (NEW finding) = evil origin gets ACAO echoed back -> any site can make
# credentialed cross-origin calls.
# ─────────────────────────────────────────────────────────────────────────────

def acao_block(headers: httpx.Headers) -> dict[str, str | None]:
    return {
        "access-control-allow-origin": headers.get("access-control-allow-origin"),
        "access-control-allow-credentials": headers.get("access-control-allow-credentials"),
        "access-control-allow-methods": headers.get("access-control-allow-methods"),
        "access-control-allow-headers": headers.get("access-control-allow-headers"),
        "vary": headers.get("vary"),
    }


async def main() -> None:
    async with _client() as c:
        # Probe 1 — hostile origin preflight.
        evil = await c.request(
            "OPTIONS", "/auth/login",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        print("=== Preflight OPTIONS /auth/login | Origin: http://evil.example ===")
        print("status:", evil.status_code)
        for k, v in acao_block(evil.headers).items():
            print(f"  {k}: {v}")
        evil_acao = evil.headers.get("access-control-allow-origin")
        print("evil origin reflected in ACAO:", evil_acao == "http://evil.example")

        # Probe 2 — allowed origin preflight (the configured frontend).
        good = await c.request(
            "OPTIONS", "/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )
        print("=== Preflight OPTIONS /auth/login | Origin: http://localhost:5173 ===")
        print("status:", good.status_code)
        for k, v in acao_block(good.headers).items():
            print(f"  {k}: {v}")
        print("--- assessment ---")
        print("allowed origin echoed:", good.headers.get("access-control-allow-origin") == "http://localhost:5173")
        print("credentials allowed:", good.headers.get("access-control-allow-credentials") == "true")
        print("methods reflected (wildcard concern):", good.headers.get("access-control-allow-methods"))
        print("headers reflected (wildcard concern):", good.headers.get("access-control-allow-headers"))


asyncio.run(main())
