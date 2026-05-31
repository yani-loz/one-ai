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


# ───────────────────────── TC-IA-064 ─────────────────────────
# Email case-sensitivity (NEW). email-validator (EmailStr) lowercases ONLY the domain,
# preserving local-part case; email_exists / get_by_email are EXACT match. Probe:
#   1) create Foo.Bar@Example.com  -> ?
#   2) create foo.bar@example.com  -> distinct user? (both 201 = two identities/human)
#   3) GET /users -> show stored emails verbatim
#   4) login with OPPOSITE local-part case of a created user -> expect 401 (case-fragile)
# If two identities can coexist and/or login is case-fragile -> NEW correctness/security
# finding. If the system normalizes (2nd create -> 409, opposite-case login -> 200) -> NA.

SUITE = "iv"


async def main() -> None:
    s = stamp()
    org_slug = f"{SUITE}-064-{s}"
    admin_email = f"{SUITE}-064-admin-{s}@example.com"
    async with _client() as c:
        plat = await platform_login(c)
        ob = await onboard_org(c, plat, name=f"IV064 {s}", slug=org_slug, admin_email=admin_email)
        print(f"[onboard] status={ob.status_code}")
        if ob.status_code != 201:
            print(f"[onboard] body={ob.text}")
            return
        admin_token = (await login(c, admin_email, DEFAULT_PW)).json()["access_token"]

        # Unique local-part per run so we never collide with other suites.
        mixed = f"Foo.Bar.{s}@Example.com"
        lower = f"foo.bar.{s}@example.com"
        print(f"[inputs] mixed={mixed!r} lower={lower!r}")

        r1 = await create_user(c, admin_token, email=mixed, full_name="Mixed Case",
                               role="member", password=DEFAULT_PW)
        print(f"[create #1 mixed] status={r1.status_code} "
              f"stored_email={r1.json().get('email') if r1.status_code == 201 else r1.text}")

        r2 = await create_user(c, admin_token, email=lower, full_name="Lower Case",
                               role="member", password=DEFAULT_PW)
        print(f"[create #2 lower] status={r2.status_code} "
              f"stored_email={r2.json().get('email') if r2.status_code == 201 else r2.text}")

        both_created = r1.status_code == 201 and r2.status_code == 201
        print(f"[DISTINCT IDENTITIES FOR SAME HUMAN] both_201={both_created}")

        lst = await c.get("/users", headers=bearer(admin_token))
        stored = sorted(u["email"] for u in lst.json()) if lst.status_code == 200 else []
        print(f"[GET /users] stored_emails={stored}")

        # ── Login case-fragility, ISOLATED ──
        # The two creates above already share an address, so a lowercase login would
        # match the lowercase twin and muddy the result. Create a THIRD user that has
        # NO twin (mixed-case local-part only), then log in with the opposite (lower)
        # case. With no twin to match, an exact-match login lookup must MISS -> 401.
        solo_mixed = f"Solo.User.{s}@Example.com"
        solo_lower_attempt = f"solo.user.{s}@example.com"
        r3 = await create_user(c, admin_token, email=solo_mixed, full_name="Solo Mixed",
                               role="member", password=DEFAULT_PW)
        solo_stored = r3.json().get("email") if r3.status_code == 201 else r3.text
        print(f"[create #3 solo-mixed] status={r3.status_code} stored_email={solo_stored!r}")

        lg_opp = await login(c, solo_lower_attempt, DEFAULT_PW)
        print(f"[login solo-user w/ OPPOSITE-case local-part] stored={solo_stored!r} "
              f"attempt={solo_lower_attempt!r} status={lg_opp.status_code} body={lg_opp.text}")

        lg_exact = await login(c, solo_stored, DEFAULT_PW)
        print(f"[login solo-user w/ EXACT case] attempt={solo_stored!r} "
              f"status={lg_exact.status_code} (control — must be 200)")


asyncio.run(main())
