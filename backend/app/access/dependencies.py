"""
Role: FastAPI dependencies for the access module — the person-scoped READER session (the PF-01
      retrieval seam on the SELECT-only oneai_reader pool).
Used by: the future Ask/retrieval routes (get_person_scoped_session is THE seam they must use);
      tests/access. The promotion route deliberately does NOT use this — promotion is a WRITE
      flow and runs on the app plane with explicit ownership checks.
Depends on: app.core.database (reader_session — the reader pool ONLY; this package never
      imports the global engine, AC18), app.identity.dependencies (verified Principal),
      .repositories.source_identity_repository.
Key invariants:
  - The person GUC derives ONLY from a VERIFIED 'auth' binding of the JWT's user id
    (principal_source_identity) — never a header, never a body, never an unverified row (AC20).
  - NO binding ⇒ person-less reader session: the visibility policies then serve org-scope rows
    only (AC3 fail-closed) — an unbound user sees shared content, never someone else's
    restricted rows.
  - READ-ONLY by construction: the reader role holds no DML on tenant tables, so a handler on
    this dependency cannot write even by accident (audit INSERT for telemetry aside). The
    unit-of-work still commits on success for the audit rows telemetry appends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.repositories.source_identity_repository import SourceIdentityRepository
from app.core.database import reader_session
from app.identity.dependencies import get_current_principal
from app.identity.exceptions import TokenInvalidError
from app.identity.principal import Principal


async def resolve_bound_person_id(
    session: AsyncSession, org_id: UUID, user_id: UUID
) -> UUID | None:
    """The person bound to `user_id` through a VERIFIED 'auth' identity, else None."""
    return await SourceIdentityRepository(session).get_verified_person_id(
        org_id, "auth", str(user_id)
    )


async def get_person_scoped_session(
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield a READER session carrying BOTH scope GUCs (org + the caller's bound person).

    Resolves the caller's person through the verified auth binding on a person-less reader
    session first (principal_source_identity is org-RLS'd and reader-SELECTable, so the lookup
    itself is tenant-safe), then opens the working session with the person GUC bound per
    transaction. An unbound caller gets a person-less session — fail-closed to org-scope rows,
    never an error.
    """
    if principal.org_id is None:
        raise TokenInvalidError("Access token is missing an organization scope.")
    async with reader_session(principal.org_id) as lookup_session:
        person_id = await resolve_bound_person_id(
            lookup_session, principal.org_id, principal.subject_id
        )
    async with reader_session(principal.org_id, person_id) as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
