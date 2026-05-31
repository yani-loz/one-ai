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
# TC-IA-055 — onboarding integrity race (CONFIRMS_FIXED; closes audit's untested branch)
#
# (a) SAME-SLUG race: fire 2 CONCURRENT POST /platform/orgs with the SAME slug (different
#     admin emails). Expect one 201 + one 409 (DuplicateOrganizationError via the slug
#     pre-check OR the IntegrityError catch at platform_auth_service.py:145), and EXACTLY
#     ONE org with that slug afterward (list_organizations). >=30 trials.
#
# (b1) LITERAL existing-email: new slug + an admin_email that already exists -> expect 409
#      and NO org with that slug (the email_exists pre-check at :138 fires BEFORE any org
#      INSERT, so trivially no orphan).
#
# (b2) CONCURRENT existing-email (the advisor's branch-closing variant): fire 2 onboards
#      with DIFFERENT new slugs but the SAME brand-new admin_email, together. Both pass the
#      email pre-check, both INSERT their org, then one user-INSERT wins and the loser hits
#      IntegrityError on users.email -> its whole tx (incl. the already-inserted org) must
#      roll back (the catch at :160). Expect one 201 + one 409 AND the loser's slug absent
#      from list_organizations (NO ORPHAN ORG). This is the path AUD-05's "untested branch"
#      covers. Orphan-checked via list_organizations + psql.
#
# Any 500, or an orphan org for a 409'd onboard, => NEW/REFUTES_FIX.
# ─────────────────────────────────────────────────────────────────────────────

NS = "race"
TRIALS_A = 30


async def _slug_count(c: httpx.AsyncClient, plat: str, slug: str) -> int:
    r = await c.get("/platform/orgs", headers=bearer(plat))
    r.raise_for_status()
    return sum(1 for o in r.json() if o["slug"] == slug)


async def main() -> None:
    run = stamp()
    async with _client() as c:
        plat = await platform_login(c)

        # ───────────────────────── (a) same-slug concurrent ─────────────────────────
        a_pair_dist: dict[str, int] = {}
        a_bad: list[dict] = []  # 500, wrong pair, or != 1 org committed
        for t in range(TRIALS_A):
            tag = f"{NS}-{run}-55a-{t}"
            slug = tag

            async def onb(idx: int) -> httpx.Response:
                return await onboard_org(
                    c, plat, name=f"Race055a {tag} {idx}", slug=slug,
                    admin_email=f"{tag}-{idx}@t.example",
                )

            res = await asyncio.gather(onb(0), onb(1), return_exceptions=True)
            codes = sorted(("EXC" if isinstance(r, BaseException) else r.status_code for r in res), key=str)
            key = "+".join(str(x) for x in codes)
            a_pair_dist[key] = a_pair_dist.get(key, 0) + 1
            committed = await _slug_count(c, plat, slug)
            if 500 in set(codes) or set(codes) != {201, 409} or committed != 1:
                bodies = ["EXC" if isinstance(r, BaseException) else r.text for r in res]
                a_bad.append({"t": t, "codes": codes, "orgs_with_slug": committed, "bodies": bodies})

        # ───────────────────── (b1) literal: new slug + EXISTING email ─────────────────────
        base_tag = f"{NS}-{run}-55b1"
        existing_email = f"{base_tag}-existing@t.example"
        seed = await onboard_org(c, plat, name=f"Race055b1 seed", slug=f"{base_tag}-seed", admin_email=existing_email)
        b1_seed_ok = seed.status_code == 201
        b1_new_slug = f"{base_tag}-newslug"
        b1 = await onboard_org(c, plat, name="Race055b1 dup-email", slug=b1_new_slug, admin_email=existing_email)
        b1_code = b1.status_code
        b1_body = b1.text
        b1_orphan = await _slug_count(c, plat, b1_new_slug)  # expect 0

        # ──────── (b2) concurrent: DIFFERENT new slugs + SAME brand-new email ────────
        # This forces both org INSERTs through, then collides on users.email -> the loser's
        # org INSERT must roll back (the IntegrityError branch at :160 — audit's untested path).
        b2_results: list[dict] = []
        b2_orphans: list[dict] = []
        B2_TRIALS = 20
        for t in range(B2_TRIALS):
            tag = f"{NS}-{run}-55b2-{t}"
            shared_email = f"{tag}-shared@t.example"
            slug0 = f"{tag}-s0"
            slug1 = f"{tag}-s1"

            async def onb_email(slug: str, idx: int) -> httpx.Response:
                return await onboard_org(
                    c, plat, name=f"Race055b2 {tag} {idx}", slug=slug, admin_email=shared_email,
                )

            res = await asyncio.gather(onb_email(slug0, 0), onb_email(slug1, 1), return_exceptions=True)
            codes = [("EXC" if isinstance(r, BaseException) else r.status_code) for r in res]
            # which slug(s) got committed?
            c0 = await _slug_count(c, plat, slug0)
            c1 = await _slug_count(c, plat, slug1)
            committed_orgs = c0 + c1
            b2_results.append({"t": t, "codes": codes, "slug0_orgs": c0, "slug1_orgs": c1})
            # Expect: one 201 + one 409, and exactly ONE org committed (loser's org rolled back).
            if 500 in codes or sorted(str(x) for x in codes) != ["201", "409"] or committed_orgs != 1:
                b2_orphans.append({"t": t, "codes": codes, "committed_orgs": committed_orgs, "slug0": slug0, "slug1": slug1})

    # ─────────────────────────────── report ───────────────────────────────
    print("=" * 70)
    print(f"TC-IA-055  run={run}")
    print("=" * 70)
    print(f"(a) same-slug concurrent  trials={TRIALS_A}")
    print(f"    status-pair dist : {a_pair_dist}")
    print(f"    bad trials (500 / wrong pair / != 1 org) : {len(a_bad)}")
    for b in a_bad[:8]:
        print("       ", b)
    print("-" * 70)
    print(f"(b1) literal new-slug + EXISTING email")
    print(f"    seed onboarded   : {b1_seed_ok}")
    print(f"    dup-email onboard code : {b1_code}  (expect 409)")
    print(f"    body             : {b1_body[:160]}")
    print(f"    orphan orgs with new slug : {b1_orphan}  (expect 0)")
    print("-" * 70)
    print(f"(b2) concurrent DIFFERENT slugs + SAME new email  trials={B2_TRIALS}")
    b2_codepairs: dict[str, int] = {}
    for r in b2_results:
        k = "+".join(sorted(str(x) for x in r["codes"]))
        b2_codepairs[k] = b2_codepairs.get(k, 0) + 1
    print(f"    status-pair dist : {b2_codepairs}")
    print(f"    sample (first 6) : ")
    for r in b2_results[:6]:
        print("       ", r)
    print(f"    ORPHAN/anomaly trials (committed_orgs != 1, 500, or wrong pair) : {len(b2_orphans)}")
    for o in b2_orphans[:8]:
        print("       ", o)
    print("=" * 70)
    a_ok = (not a_bad) and a_pair_dist.get("201+409", 0) > 0
    b1_ok = (b1_code == 409 and b1_orphan == 0)
    b2_ok = (not b2_orphans) and b2_codepairs.get("201+409", 0) > 0
    if a_ok and b1_ok and b2_ok:
        print("VERDICT: CONFIRMS_FIXED — (a) 1 org/slug + 201+409, (b1) 409 no orphan, (b2) IntegrityError branch rolls back loser org (no orphan)")
    else:
        flags = []
        if not a_ok: flags.append("(a) FAIL")
        if not b1_ok: flags.append("(b1) FAIL")
        if not b2_ok: flags.append("(b2) FAIL — orphan org or 500 on integrity branch (NEW/REFUTES_FIX)")
        print("VERDICT: " + "; ".join(flags))


asyncio.run(main())
