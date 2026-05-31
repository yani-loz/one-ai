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


# ======================== TC-IA-016 test logic ========================
#
# Part 1: assert no password/hash/secret field leaks in any login response.
# Part 2: best-effort constant-time timing sanity (known-existing+wrong-pw vs ghost).

import statistics

WRONG_PW = "WRONG-Pass-9999!"
SECRET_FIELD_HINTS = ("password", "hash", "secret", "salt")


def scan_for_secrets(obj, path=""):
    """Recursively collect any dict keys that look like a credential/hash leak."""
    hits = []
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


async def _timed_login(c, email, password):
    """Return elapsed milliseconds for one /auth/login call."""
    t0 = time.perf_counter()
    await login(c, email, password)
    return (time.perf_counter() - t0) * 1000.0


async def main():
    s = stamp()
    slug = f"authn016-{s}"
    real_email = f"admin-authn016-{s}@oneai.dev"
    ghost_email = f"ghost-authn016-{s}@oneai.dev"

    async with _client() as c:
        plat = await platform_login(c)
        ob = await onboard_org(
            c, plat, name=f"authn016 co-{s}", slug=slug, admin_email=real_email,
            admin_name="Authn016 Admin",
        )
        print(f"== onboard == {ob.status_code} admin={real_email}")

        # -- Part 1: leak scan --
        success = await login(c, real_email, DEFAULT_PW)
        failure = await login(c, real_email, WRONG_PW)
        succ_keys = scan_for_secrets(success.json())
        fail_keys = scan_for_secrets(failure.json())
        # A bcrypt hash always starts with $2b$/$2a$; assert it never appears in any body.
        hash_marker = "$2b$" in success.text or "$2a$" in success.text \
            or "$2b$" in failure.text or "$2a$" in failure.text
        print("-- leak scan --")
        print(f"success body secret keys: {succ_keys if succ_keys else 'NONE'}")
        print(f"failure body secret keys: {fail_keys if fail_keys else 'NONE'}")
        print(f"bcrypt hash marker ($2b$/$2a$) present in any body: {hash_marker}")

        # -- Part 2: timing sanity --
        for _ in range(5):  # warmup
            await _timed_login(c, real_email, WRONG_PW)
            await _timed_login(c, ghost_email, WRONG_PW)

        real_samples = [await _timed_login(c, real_email, WRONG_PW) for _ in range(20)]
        ghost_samples = [await _timed_login(c, ghost_email, WRONG_PW) for _ in range(20)]

        rm, gm = statistics.mean(real_samples), statistics.mean(ghost_samples)
        print("-- timing (20 samples each, after 5 warmup) --")
        print(f"real-email + WRONG password : mean={rm:.2f} ms  "
              f"median={statistics.median(real_samples):.2f} ms  "
              f"stdev={statistics.stdev(real_samples):.2f} ms")
        print(f"ghost-email (never-existing): mean={gm:.2f} ms  "
              f"median={statistics.median(ghost_samples):.2f} ms  "
              f"stdev={statistics.stdev(ghost_samples):.2f} ms")
        ratio = (max(rm, gm) / min(rm, gm)) if min(rm, gm) > 0 else float("inf")
        same_order = ratio < 3.0
        print(f"delta(mean) = {abs(rm - gm):.2f} ms  ratio = {ratio:.2f}x  "
              f"(same order of magnitude: {same_order})")

        no_leak = not succ_keys and not fail_keys and not hash_marker
        print(f"RESULT: {'PASS-WITH-CONCERN' if no_leak else 'FAIL'} "
              f"(no leak; timing comparable but not a hard proof)")


asyncio.run(main())
