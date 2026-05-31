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
# TC-IA-050 — last-admin race: concurrent DELETE admin1 + DELETE admin2 → 0 admins
#
# Per iteration (fresh run-stamped org):
#   1. platform admin onboards org -> admin1 (company_admin) + org_id
#   2. admin1 logs in -> real company_admin access token
#   3. admin1 creates admin2 (role=company_admin) in the same org  (precondition: 2 admins)
#   4. fire CONCURRENT  DELETE /users/{admin1}  +  DELETE /users/{admin2}  with admin1's token
#   5. read DB ground-truth: count active company_admins in that org (psql is the oracle;
#      the harness can't psql, so it prints the org_id + a forged-token GET /users as a
#      secondary read, and the runner cross-checks via psql).
#
# The guard (_guard_last_admin, user_service.py:123) is non-atomic check-then-act:
# count_active_admins(exclude=target) SELECT, then UPDATE+flush, commit deferred to the
# request's get_tenant_session. Two concurrent deletes each EXCLUDE the OTHER admin, so
# both see count=1 (the peer still active in their snapshot), both pass the guard, both
# deactivate, both commit -> 0 active admins = org locked out of its own user management.
# A correct atomic guard would make exactly ONE delete 409.
# ─────────────────────────────────────────────────────────────────────────────

NS = "race"
ITERATIONS = 50


async def main() -> None:
    run = stamp()
    zero_admin_iters: list[dict] = []
    setup_fail_iters: list[dict] = []
    status_pairs: dict[str, int] = {}
    org_ids: list[str] = []

    async with _client() as c:
        plat = await platform_login(c)

        for i in range(ITERATIONS):
            tag = f"{NS}-{run}-50-{i}"
            slug = f"{tag}"
            a1_email = f"{tag}-a1@t.example"
            a2_email = f"{tag}-a2@t.example"

            onb = await onboard_org(
                c, plat, name=f"Race050 {tag}", slug=slug, admin_email=a1_email,
            )
            if onb.status_code != 201:
                setup_fail_iters.append({"i": i, "stage": "onboard", "code": onb.status_code, "body": onb.text})
                continue
            org_id = onb.json()["organization"]["id"]
            admin1_id = onb.json()["admin"]["id"]
            org_ids.append(org_id)

            lr = await login(c, a1_email, DEFAULT_PW)
            if lr.status_code != 200:
                setup_fail_iters.append({"i": i, "stage": "login", "code": lr.status_code, "body": lr.text})
                continue
            a1_token = lr.json()["access_token"]

            cr = await create_user(
                c, a1_token, email=a2_email, full_name="Admin Two", role="company_admin",
            )
            if cr.status_code != 201:
                setup_fail_iters.append({"i": i, "stage": "create_admin2", "code": cr.status_code, "body": cr.text})
                continue
            admin2_id = cr.json()["id"]

            # Precondition: exactly 2 active company_admins now exist (verified via forged read).
            forged = forge_company_token(sub=admin1_id, org_id=org_id, role="company_admin")
            pre = await c.get("/users", headers=bearer(forged))
            pre_admins = (
                sum(1 for u in pre.json() if u["role"] == "company_admin" and u["is_active"])
                if pre.status_code == 200 else -1
            )
            if pre_admins != 2:
                setup_fail_iters.append({"i": i, "stage": "precondition", "pre_admins": pre_admins, "org_id": org_id})
                continue

            # ── THE RACE: only the two mutating calls are gathered ──
            async def del_user(uid: str) -> httpx.Response:
                return await c.delete(f"/users/{uid}", headers=bearer(a1_token))

            res = await asyncio.gather(
                del_user(admin1_id), del_user(admin2_id), return_exceptions=True,
            )
            codes = tuple(
                "EXC" if isinstance(r, BaseException) else r.status_code for r in res
            )
            pair_key = "+".join(str(x) for x in sorted(codes, key=str))
            status_pairs[pair_key] = status_pairs.get(pair_key, 0) + 1

            # Ground-truth read via forged token (authz reads JWT, not DB active-state).
            post = await c.get("/users", headers=bearer(forged))
            post_admins = (
                sum(1 for u in post.json() if u["role"] == "company_admin" and u["is_active"])
                if post.status_code == 200 else -1
            )

            if post_admins == 0:
                zero_admin_iters.append(
                    {"i": i, "org_id": org_id, "codes": codes, "active_admins_after": post_admins}
                )

    print("=" * 70)
    print(f"TC-IA-050  run={run}  iterations={ITERATIONS}")
    print("=" * 70)
    print(f"setup failures           : {len(setup_fail_iters)}")
    if setup_fail_iters:
        for s in setup_fail_iters[:5]:
            print("   ", s)
    print(f"status-pair distribution : {status_pairs}")
    print(f"ZERO-ADMIN iterations    : {len(zero_admin_iters)}  (LOCKOUT — the win)")
    for z in zero_admin_iters[:8]:
        print("   ", z)
    print("-" * 70)
    print("Sample org_ids for psql ground-truth cross-check (first 8):")
    for oid in org_ids[:8]:
        print("   ", oid)
    print("=" * 70)
    print(
        f"VERDICT: {'GUARD BROKE — last-admin race fired' if zero_admin_iters else 'no lockout observed'}"
        f" ({len(zero_admin_iters)}/{ITERATIONS - len(setup_fail_iters)} firing iterations)"
    )


asyncio.run(main())
