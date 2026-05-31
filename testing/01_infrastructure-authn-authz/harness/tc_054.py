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
# TC-IA-054 — create-user duplicate race (CONFIRMS_FIXED = AUD-05)
#
# Per trial: fresh org + admin (login -> token). Fire 2 CONCURRENT POST /users with the
# SAME new email. The check-then-insert (email_exists pre-check, then add/flush against
# the users.email UNIQUE constraint) is a TOCTOU; the loser must hit IntegrityError,
# which create_user catches (user_service.py:75) and maps to DuplicateUserError -> 409.
# Contract: exactly one 201 + one 409, NEVER a 500. Any 500 => REFUTES_FIX.
#
# Race-engagement note: 201+409 alone cannot distinguish the IntegrityError branch from
# the serial pre-check (both yield 409). For the contract verdict (never 500) either
# path is acceptable. To PROVE the IntegrityError->409 conversion actually executed at
# least once, the runner greps `docker compose logs backend` for the IntegrityError /
# rollback signature after this run.
# ─────────────────────────────────────────────────────────────────────────────

NS = "race"
TRIALS = 50


async def main() -> None:
    run = stamp()
    setup_fail: list[dict] = []
    pair_dist: dict[str, int] = {}
    bad_trials: list[dict] = []  # any 500, or not exactly {201,409}

    async with _client() as c:
        plat = await platform_login(c)

        for t in range(TRIALS):
            tag = f"{NS}-{run}-54-{t}"
            a_email = f"{tag}-admin@t.example"
            onb = await onboard_org(c, plat, name=f"Race054 {tag}", slug=tag, admin_email=a_email)
            if onb.status_code != 201:
                setup_fail.append({"t": t, "stage": "onboard", "code": onb.status_code})
                continue
            lr = await login(c, a_email, DEFAULT_PW)
            if lr.status_code != 200:
                setup_fail.append({"t": t, "stage": "login", "code": lr.status_code})
                continue
            token = lr.json()["access_token"]

            dup_email = f"{tag}-dup@t.example"

            async def make_user(_i: int) -> httpx.Response:
                return await create_user(c, token, email=dup_email, full_name="Dup User", role="member")

            res = await asyncio.gather(make_user(0), make_user(1), return_exceptions=True)
            codes = sorted(
                ("EXC" if isinstance(r, BaseException) else r.status_code for r in res), key=str
            )
            pair_key = "+".join(str(x) for x in codes)
            pair_dist[pair_key] = pair_dist.get(pair_key, 0) + 1

            code_set = set(codes)
            if 500 in code_set or code_set != {201, 409}:
                bodies = ["EXC" if isinstance(r, BaseException) else r.text for r in res]
                bad_trials.append({"t": t, "codes": codes, "bodies": bodies})

    print("=" * 70)
    print(f"TC-IA-054  run={run}  trials={TRIALS}  (2 concurrent POST /users, same email)")
    print("=" * 70)
    print(f"setup failures        : {len(setup_fail)}")
    for s in setup_fail[:5]:
        print("   ", s)
    print(f"status-pair dist      : {pair_dist}")
    print(f"bad trials (500 / !=  {{201,409}}) : {len(bad_trials)}")
    for b in bad_trials[:10]:
        print("   ", b)
    print("=" * 70)
    n_ok = TRIALS - len(setup_fail)
    if any("500" in k for k in pair_dist):
        print("VERDICT: REFUTES_FIX — a 500 appeared on the duplicate-create race (AUD-05 NOT held)")
    elif not bad_trials and pair_dist.get("201+409", 0) > 0:
        print(f"VERDICT: CONFIRMS_FIXED — every race trial = 201+409, no 500 ({pair_dist.get('201+409',0)}/{n_ok})")
    else:
        print("VERDICT: inconclusive — inspect pair distribution")


asyncio.run(main())
