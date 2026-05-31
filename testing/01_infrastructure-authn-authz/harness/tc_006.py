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
# TC-IA-006 — Error paths leak no stack/secret (Negative). Sev Medium if a leak.
# Probes:
#   A) GET unknown route -> 404
#   B) wrong method (DELETE /health) -> 405
#   C) malformed JSON to /auth/login -> 422 (benign payload, NO secret-like strings)
#   D) wrong Content-Type to /auth/login -> 422
# Then scan EVERY error body for leakage markers: the jwt secret, the DB DSN /
# postgres password / asyncpg URL, and python traceback markers. Any hit = a leak
# (the win). The request bodies are benign so the scan can't false-positive on our
# own input.
# ─────────────────────────────────────────────────────────────────────────────

# Leakage markers we must NEVER see in an error body.
LEAK_MARKERS = [
    "dev-only-insecure-secret-change-me-in-prod",  # the jwt secret
    "postgresql+asyncpg",                            # the async DSN scheme
    "postgres://",                                   # any DSN scheme
    ":oneai@",                                        # postgres password embedded in DSN
    "Traceback (most recent call last)",            # python stack trace
    'File "/app',                                    # stack frame file path
    "sqlalchemy.exc",                                # ORM exception class leak
    "asyncpg",                                        # driver internals
]


def scan(body: str) -> list[str]:
    return [m for m in LEAK_MARKERS if m in body]


async def main() -> None:
    async with _client() as c:
        results = []

        # A) unknown route -> 404
        a = await c.get("/this-route-does-not-exist-xyz")
        results.append(("A unknown route GET /this-route-does-not-exist-xyz", 404, a))

        # B) wrong method -> 405
        b = await c.request("DELETE", "/health")
        results.append(("B wrong method DELETE /health", 405, b))

        # C) malformed JSON -> 422 (benign: not valid JSON, no sensitive strings)
        c_resp = await c.post(
            "/auth/login",
            content=b"{not-valid-json",
            headers={"content-type": "application/json"},
        )
        results.append(("C malformed JSON POST /auth/login", 422, c_resp))

        # D) wrong Content-Type (text/plain instead of application/json) -> 422
        d = await c.post(
            "/auth/login",
            content=b"email=x&password=y",
            headers={"content-type": "text/plain"},
        )
        results.append(("D wrong Content-Type POST /auth/login", 422, d))

        any_leak = False
        for label, expected, r in results:
            print(f"=== {label} ===")
            print("  status:", r.status_code, "(expected", expected, ")")
            print("  status matches:", r.status_code == expected)
            print("  content-type:", r.headers.get("content-type"))
            print("  body:", r.text[:600])
            hits = scan(r.text)
            print("  LEAK MARKERS FOUND:", hits if hits else "none")
            if hits:
                any_leak = True
        print("=== SUMMARY ===")
        print("any secret/stack/DSN leak across all error bodies:", any_leak)


asyncio.run(main())
