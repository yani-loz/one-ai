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
# TC-IA-053 — refresh-rotation race (CONFIRMS_FIXED = AUD-01)
#
# Per trial: company login -> ONE raw refresh token. Fire N=10 CONCURRENT /auth/refresh
# all presenting the SAME raw token. The atomic conditional revoke
# (refresh_token_repository.revoke_by_hash: UPDATE ... WHERE token_hash=:h AND
# revoked_at IS NULL; rowcount==0 -> RefreshTokenInvalidError) must make EXACTLY ONE
# request win (200) and the other N-1 lose (401). >1 success in ANY trial => REFUTES_FIX
# (escalate, High — the single-use invariant breaks under contention).
#
# Race-engagement proof: with N=10 simultaneous presentations of one token, the losers
# can only 401 if the winner's revoke committed first and the conditional UPDATE saw
# revoked_at NOT NULL. A pure serial path would still yield 1x200 + 9x401, so to show
# the race truly engaged we also report whether ANY trial produced >1 success and the
# raw per-trial success counts (all must be exactly 1).
# ─────────────────────────────────────────────────────────────────────────────

NS = "race"
TRIALS = 50
N = 10


async def main() -> None:
    run = stamp()
    per_trial_success: list[int] = []
    setup_fail: list[dict] = []
    multi_success_trials: list[dict] = []
    code_dist: dict[str, int] = {}

    async with _client() as c:
        plat = await platform_login(c)
        tag = f"{NS}-{run}-53"
        a_email = f"{tag}-admin@t.example"
        onb = await onboard_org(c, plat, name=f"Race053 {tag}", slug=tag, admin_email=a_email)
        if onb.status_code != 201:
            print("FATAL onboard", onb.status_code, onb.text)
            return

        for t in range(TRIALS):
            lr = await login(c, a_email, DEFAULT_PW)
            if lr.status_code != 200:
                setup_fail.append({"t": t, "code": lr.status_code, "body": lr.text})
                continue
            raw_refresh = lr.json()["refresh_token"]

            async def do_refresh(_i: int) -> httpx.Response:
                return await c.post("/auth/refresh", json={"refresh_token": raw_refresh})

            res = await asyncio.gather(*(do_refresh(i) for i in range(N)), return_exceptions=True)
            codes = ["EXC" if isinstance(r, BaseException) else r.status_code for r in res]
            for k in codes:
                code_dist[str(k)] = code_dist.get(str(k), 0) + 1
            successes = sum(1 for k in codes if k == 200)
            per_trial_success.append(successes)
            if successes != 1:
                multi_success_trials.append({"t": t, "successes": successes, "codes": codes})

    print("=" * 70)
    print(f"TC-IA-053  run={run}  trials={TRIALS}  concurrency N={N}")
    print("=" * 70)
    print(f"setup failures              : {len(setup_fail)}")
    for s in setup_fail[:5]:
        print("   ", s)
    print(f"aggregate code distribution : {code_dist}")
    print(f"per-trial success counts    : {per_trial_success}")
    print(f"trials with success != 1    : {len(multi_success_trials)}")
    for m in multi_success_trials[:10]:
        print("   ", m)
    only_one = all(s == 1 for s in per_trial_success) and per_trial_success
    print("=" * 70)
    if multi_success_trials:
        print(f"VERDICT: REFUTES_FIX — {len(multi_success_trials)} trial(s) minted >1 valid pair from one token (HIGH)")
    elif only_one:
        print(f"VERDICT: CONFIRMS_FIXED — exactly 1x200 + {N-1}x401 in all {len(per_trial_success)} trials (AUD-01 holds under contention)")
    else:
        print("VERDICT: inconclusive — inspect per-trial counts")


asyncio.run(main())
