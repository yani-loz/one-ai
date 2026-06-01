"""
Shared harness for Targets 07 (break-glass, PC-05) + 08 (erasure, PC-06). Identical copy
lives in testing/08_erasure/harness/_common.py — both inline it over stdin.

HOW SCRIPTS RUN (no filesystem imports): each per-case script INLINES this file's contents at
the top, appends its own `async def main(): ...` + `asyncio.run(main())`, and is run with:

    docker compose exec -T backend python - < testing/0N_<target>/harness/<script>.py

Everything talks to the REAL running uvicorn at http://localhost:8000.

Isolation rules (DB is persistent + shared):
  - Provision FRESH, run-stamped orgs + unique emails via `stamp()`. NEVER mutate the demo
    platform admin (super@ethera.ai).
  - **NEVER suspend / legal-hold / ERASE the demo or globex orgs (or any org you didn't
    onboard).** Erasure is IRREVERSIBLE (deletes users + tokens, offboards). Erase ONLY your
    own fresh run-stamped orgs.

Key facts: RLS inert → JWT secret is the single isolation layer; the dev JWT secret is the
forgeable default (DEV_SECRET) so forged platform/company tokens are a REAL capability.
Break-glass: a platform admin REQUESTS, a company_admin of the TARGET org APPROVES (consent —
no platform approve path); transitions are state-guarded (409) + row-locked; expiry is live
(approved AND now < expires_at, 4h window). Erasure: platform-only; legal-hold-beats-erasure
(409, nothing touched); slug-confirm guard (400); atomic; append-only audit_log retained.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt

BASE = "http://localhost:8000"
DEV_SECRET = "dev-only-insecure-secret-change-me-in-prod"
ALG = "HS256"
COMPANY_AUD = "company"
PLATFORM_AUD = "platform"
PLATFORM_EMAIL = "super@ethera.ai"
PLATFORM_PW = "Sup3r-Dev-Only-2026!"
DEFAULT_PW = "Valid-Pass-2026!"

GRANT_REQUESTED = "requested"
GRANT_APPROVED = "approved"
GRANT_DENIED = "denied"
GRANT_REVOKED = "revoked"


def stamp() -> str:
    """Short lowercase-alnum run id (slug-safe) so each run's data is unique."""
    return f"{int(time.time() * 1000):x}{uuid4().hex[:4]}"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE, timeout=timeout)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# — Platform auth + org provisioning —

async def platform_login_pair(c: httpx.AsyncClient) -> tuple[str, str]:
    """Log in the demo platform admin; return (access_token, refresh_token)."""
    r = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
    r.raise_for_status()
    b = r.json()
    return b["access_token"], b["refresh_token"]


async def platform_login(c: httpx.AsyncClient) -> str:
    access, _ = await platform_login_pair(c)
    return access


async def onboard_org(c, plat_token, *, name, slug, admin_email, admin_name="Org Admin", admin_pw=DEFAULT_PW):
    """Onboard a new org + first company_admin. Raw response."""
    return await c.post("/platform/orgs", headers=bearer(plat_token), json={
        "org_name": name, "org_slug": slug, "admin_email": admin_email,
        "admin_full_name": admin_name, "admin_password": admin_pw,
    })


async def login(c, email, password):
    """Company-user login (/auth/login). Raw response."""
    return await c.post("/auth/login", json={"email": email, "password": password})


async def company_login_pair(c, email, password=DEFAULT_PW) -> tuple[str, str]:
    r = await login(c, email, password)
    r.raise_for_status()
    b = r.json()
    return b["access_token"], b["refresh_token"]


async def provision_company(c, plat_token, prefix) -> dict:
    """Onboard one fresh run-stamped org + admin, log the admin in. Returns org_id, slug,
    admin_email, admin_access, admin_refresh. The org is YOURS — safe to suspend/hold/erase."""
    slug = f"{prefix}-{stamp()}"
    admin_email = f"admin-{slug}@oneai.dev"
    r = await onboard_org(c, plat_token, name=f"Org {slug}", slug=slug, admin_email=admin_email)
    r.raise_for_status()
    org_id = r.json()["organization"]["id"]
    access, refresh = await company_login_pair(c, admin_email, DEFAULT_PW)
    return {"org_id": org_id, "slug": slug, "admin_email": admin_email,
            "admin_access": access, "admin_refresh": refresh}


# — PC-05 break-glass support access —

async def request_support(c, plat_token, org_id, reason="break-glass: incident investigation"):
    """Platform: POST /platform/orgs/{id}/support-requests (creates a `requested` grant)."""
    return await c.post(f"/platform/orgs/{org_id}/support-requests", headers=bearer(plat_token),
                        json={"reason": reason})


async def list_my_requests(c, plat_token):
    """Platform: GET /platform/support-requests (this admin's requests)."""
    return await c.get("/platform/support-requests", headers=bearer(plat_token))


async def platform_revoke_request(c, plat_token, grant_id):
    """Platform: POST /platform/support-requests/{id}/revoke (own request)."""
    return await c.post(f"/platform/support-requests/{grant_id}/revoke", headers=bearer(plat_token))


async def company_inbox(c, admin_token):
    """Company: GET /support-access (the org's approval inbox)."""
    return await c.get("/support-access", headers=bearer(admin_token))


async def company_approve(c, admin_token, grant_id):
    """Company: POST /support-access/{id}/approve (the ONLY approval path — consent)."""
    return await c.post(f"/support-access/{grant_id}/approve", headers=bearer(admin_token))


async def company_deny(c, admin_token, grant_id):
    return await c.post(f"/support-access/{grant_id}/deny", headers=bearer(admin_token))


async def company_revoke(c, admin_token, grant_id):
    return await c.post(f"/support-access/{grant_id}/revoke", headers=bearer(admin_token))


# — PC-06 erasure + compliance export —

async def erase_org(c, plat_token, org_id, *, confirm_slug, reason="GDPR offboarding (test)"):
    """Platform: POST /platform/orgs/{id}/erase (IRREVERSIBLE — only your own run-stamped orgs)."""
    return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                        json={"reason": reason, "confirm_slug": confirm_slug})


async def compliance_export(c, plat_token, org_id):
    """Platform: GET /platform/orgs/{id}/compliance-export (metadata + audit trail)."""
    return await c.get(f"/platform/orgs/{org_id}/compliance-export", headers=bearer(plat_token))


async def get_org_audit(c, plat_token, org_id, limit=200):
    """Platform: GET /platform/orgs/{id}/audit (the org's audit trail)."""
    return await c.get(f"/platform/orgs/{org_id}/audit?limit={limit}", headers=bearer(plat_token))


# — Lifecycle helpers (PC-03a — for legal-hold/status setup in erasure tests) —

async def patch_legal_hold(c, plat_token, org_id, legal_hold: bool):
    return await c.patch(f"/platform/orgs/{org_id}/legal-hold", headers=bearer(plat_token),
                        json={"legal_hold": legal_hold})


async def patch_status(c, plat_token, org_id, status: str):
    return await c.patch(f"/platform/orgs/{org_id}/status", headers=bearer(plat_token),
                        json={"status": status})


async def get_org_detail(c, plat_token, org_id):
    return await c.get(f"/platform/orgs/{org_id}", headers=bearer(plat_token))


# — Token forging (isolation rests on JWT secrecy; RLS inert) —

def forge_company_token(*, sub, org_id, role="company_admin", ttl_min=15, secret=DEV_SECRET,
                        aud=COMPANY_AUD, alg=ALG, drop=(), expired=False):
    """Mint a company access token. For break-glass: forge a company_admin token with
    org_id=<target org> to test whether the consent gate rests on the JWT secret."""
    now = datetime.now(UTC)
    iat = now - timedelta(minutes=ttl_min + 5) if expired else now
    exp = iat + timedelta(minutes=ttl_min)
    claims = {"sub": sub, "type": "access", "aud": aud, "role": role, "org_id": org_id,
              "iat": int(iat.timestamp()), "exp": int(exp.timestamp()), "jti": str(uuid4())}
    for k in drop:
        claims.pop(k, None)
    if alg == "none":
        return jwt.encode(claims, key=None, algorithm="none")
    return jwt.encode(claims, secret, algorithm=alg)


def forge_platform_token(*, sub=None, ttl_min=15, secret=DEV_SECRET, alg=ALG, drop=(), expired=False):
    """Mint a platform access token (aud='platform'). With the dev secret it is a real forged
    capability (request/revoke support, erase/legal-hold any org)."""
    return forge_company_token(sub=sub or str(uuid4()), org_id=None, role="platform_admin",
                               ttl_min=ttl_min, secret=secret, aud=PLATFORM_AUD, alg=alg,
                               drop=drop, expired=expired)


# — Concurrency / stress —

async def fire_concurrent(make_coro, n: int) -> list:
    """Run n coroutines concurrently; results (exceptions captured, not raised)."""
    return await asyncio.gather(*(make_coro(i) for i in range(n)), return_exceptions=True)


def summarize(results: list) -> dict:
    """Tally HTTP status codes across responses; bucket exceptions by 'EXC:<Type>'."""
    tally: dict = {}
    for r in results:
        key = f"EXC:{type(r).__name__}" if isinstance(r, BaseException) else r.status_code
        tally[key] = tally.get(key, 0) + 1
    return tally
