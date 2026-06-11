"""
Role: DEV-ONLY idempotent seed for the Identity module — inserts the demo
      organizations (each with a company_admin + member) plus the platform admin,
      using the exact dev credentials in docs/FIX_BEFORE_PROD.md.
Used by: developers/CI demo setup. Run as a module from `backend/`:
         `docker compose exec backend uv run python -m scripts.seed_identity`
         (or `uv run python -m scripts.seed_identity` inside the backend container).
Depends on: app.core.config (settings), app.core.database (GlobalSessionLocal,
            scoped_session, engine), app.identity models, repositories, and
            security.password.hash_password.
Key invariants:
  - DEV ONLY. These accounts are public backdoors (listed in docs/FIX_BEFORE_PROD.md)
    and MUST be removed before any production deploy — see that checklist. The script
    refuses to run in ANY environment that requires secure secrets (everything except
    app_env 'local' | 'test' — the same predicate as the config boot guard), so
    staging or a typo'd app_env cannot be seeded with VCS-published credentials.
  - Idempotent: each entity is checked independently (org by slug, users + platform
    admin by email) and SKIPPED if it already exists. A partial prior run still
    completes on re-run, and adding a new company here only seeds the new rows.
  - Passwords are bcrypt-hashed via the identity module's hash_password. Raw passwords
    and hashes are NEVER printed or logged — the summary lists emails + roles only.
  - Each org's users are written on a tenant-scoped session bound to that org's GUC
    (honoring UserRepository.add's contract); the orgs and platform admin (non-tenant
    entities) are written on a plain session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.database import GlobalSessionLocal, engine, scoped_session
from app.identity.enums import UserRole
from app.identity.models.organization import Organization
from app.identity.models.platform_admin import PlatformAdmin
from app.identity.models.user import User
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.security.password import hash_password

# Reused dev passwords (same per role across companies for tester convenience).
_ADMIN_PASSWORD = "Adm1n-Dev-Only-2026!"
_MEMBER_PASSWORD = "Memb3r-Dev-Only-2026!"

@dataclass(frozen=True)
class _DemoUser:
    """A demo company user to seed into a demo org (DEV ONLY)."""

    email: str
    full_name: str
    role: UserRole
    password: str


@dataclass(frozen=True)
class _DemoCompany:
    """A demo organization plus the company users to seed into it (DEV ONLY)."""

    name: str
    slug: str
    users: tuple[_DemoUser, ...]


@dataclass(frozen=True)
class _DemoPlatformAdmin:
    """The demo platform admin to seed (DEV ONLY, separate auth domain)."""

    email: str
    full_name: str
    password: str


_DEMO_PLATFORM_ADMIN = _DemoPlatformAdmin(
    email="super@ethera.ai",
    full_name="Ethera Super Admin",
    password="Sup3r-Dev-Only-2026!",
)


def _company_users(slug: str) -> tuple[_DemoUser, ...]:
    """Build the standard (admin + member) demo user pair for org `slug`."""
    return (
        _DemoUser(
            email=f"admin@{slug}.oneai",
            full_name=f"{slug.capitalize()} Company Admin",
            role=UserRole.company_admin,
            password=_ADMIN_PASSWORD,
        ),
        _DemoUser(
            email=f"member@{slug}.oneai",
            full_name=f"{slug.capitalize()} Member",
            role=UserRole.member,
            password=_MEMBER_PASSWORD,
        ),
    )


def _demo_companies() -> tuple[_DemoCompany, ...]:
    """Return the demo companies to seed. Their ids are server-generated random UUIDs
    (gen_random_uuid()) — exactly like a real onboarded company — so the dev environment
    has no guessable/enumerable org ids, mirroring production."""
    return (
        _DemoCompany(name="One AI Demo GmbH", slug="demo", users=_company_users("demo")),
        _DemoCompany(
            name="Globex Industries GmbH", slug="globex", users=_company_users("globex")
        ),
    )


def _refuse_in_secure_env() -> None:
    """Abort in any environment that requires secure secrets — these are public backdoor creds.

    Gates on the SAME predicate as the config secret guard (Settings.requires_secure_secrets):
    only app_env 'local' | 'test' may seed. Staging, production, AND any unrecognized/typo'd
    app_env all refuse, fail-closed — the demo accounts are listed in docs/FIX_BEFORE_PROD.md
    and must never reach a real tenant; this is a hard guard, not a warning.
    """
    settings = get_settings()
    if settings.requires_secure_secrets:
        raise SystemExit(
            f"Refusing to seed demo identity data: app_env={settings.app_env!r} requires "
            "secure secrets. These DEV-ONLY accounts may only be created when app_env is "
            "'local' or 'test' (see docs/FIX_BEFORE_PROD.md)."
        )


async def _seed_platform_admin() -> None:
    """Create the platform admin on a plain session (idempotent, matched by email)."""
    async with GlobalSessionLocal() as session:
        platform_admins = PlatformAdminRepository(session)
        if await platform_admins.get_by_email(_DEMO_PLATFORM_ADMIN.email) is None:
            session.add(
                PlatformAdmin(
                    email=_DEMO_PLATFORM_ADMIN.email,
                    full_name=_DEMO_PLATFORM_ADMIN.full_name,
                    password_hash=hash_password(_DEMO_PLATFORM_ADMIN.password),
                )
            )
            print(f"  [created] platform_admin {_DEMO_PLATFORM_ADMIN.email}")
        else:
            print(f"  [skipped] platform_admin {_DEMO_PLATFORM_ADMIN.email} already exists")
        await session.commit()


async def _seed_company(company: _DemoCompany) -> None:
    """Create one demo org and its users idempotently.

    The org (the tenant root) is created on a plain session; its users on a
    tenant-scoped session bound to the org's GUC (UserRepository.add's contract). The
    org is matched by slug, each user by globally-unique email.
    """
    async with GlobalSessionLocal() as session:
        organizations = OrganizationRepository(session)
        existing = await organizations.get_by_slug(company.slug)
        if existing is None:
            created = await organizations.add(
                Organization(name=company.name, slug=company.slug)
            )
            resolved_org_id = created.id  # server-generated random UUID
            print(f"  [created] organization  {company.name!r} (slug={company.slug})")
        else:
            resolved_org_id = existing.id
            print(
                f"  [skipped] organization  {company.name!r} already exists "
                f"(id={resolved_org_id})"
            )
        await session.commit()

    async with scoped_session(resolved_org_id) as session:
        users = UserRepository(session)
        for demo_user in company.users:
            if await users.email_exists(demo_user.email):
                print(f"  [skipped] {demo_user.role.value:<13} {demo_user.email} already exists")
                continue
            await users.add(
                User(
                    org_id=resolved_org_id,
                    email=demo_user.email,
                    full_name=demo_user.full_name,
                    password_hash=hash_password(demo_user.password),
                    role=demo_user.role.value,
                )
            )
            print(f"  [created] {demo_user.role.value:<13} {demo_user.email}")
        await session.commit()


async def seed_identity() -> None:
    """Seed the demo companies, their users, and the platform admin idempotently.

    Safe to re-run: existing entities are skipped (by org slug / user + admin email),
    so a partial prior run is completed on the next invocation. Prints a summary of
    emails + roles only — never raw passwords or hashes.
    """
    _refuse_in_secure_env()
    companies = _demo_companies()

    print("Seeding DEV-ONLY demo identity data (see docs/FIX_BEFORE_PROD.md):")
    await _seed_platform_admin()
    for company in companies:
        await _seed_company(company)

    print("\nDemo accounts ready (passwords/hashes NOT shown — see docs/FIX_BEFORE_PROD.md):")
    print(f"  platform_admin : {_DEMO_PLATFORM_ADMIN.email}")
    for company in companies:
        for demo_user in company.users:
            print(f"  {demo_user.role.value:<14} : {demo_user.email}  (org={company.slug})")
    print("\nReminder: DEV-ONLY credentials. Remove before production (docs/FIX_BEFORE_PROD.md).")


async def _main() -> None:
    """Run the seed and dispose the engine cleanly (avoids 'Event loop is closed')."""
    try:
        await seed_identity()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
