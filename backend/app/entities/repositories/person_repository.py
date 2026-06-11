"""
Role: Data access for the person graph (person / person_email / person_alias) — no business
      decisions (rule A5). The get-or-create resolution LOGIC lives in the resolver (slice 3c);
      this provides the org-scoped primitives it builds on.
Used by: the entity resolver + connector ingest; constructed on the caller's tenant session.
Depends on: app.entities.models.person, SQLAlchemy async.
Key invariants:
  - EVERY read is org-scoped (filters by org_id) — a caller only ever sees its own org's people.
  - `get_person_id_by_email` expects the NORMALIZED email (the resolver normalizes before calling);
    it is the deterministic match-key lookup behind get-or-create.
  - The caller owns the transaction; methods only add/flush, never commit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.person import Person, PersonAlias, PersonEmail


class PersonRepository:
    """Org-scoped persistence for persons and their emails/aliases."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def insert(self, person: Person) -> Person:
        """Stage a new person and flush to populate the server-generated id."""
        self._session.add(person)
        await self._session.flush()
        return person

    async def get_in_org(self, person_id: UUID, org_id: UUID) -> Person | None:
        """Load a person by id iff it belongs to `org_id`, else None."""
        result = await self._session.execute(
            select(Person).where(Person.id == person_id, Person.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID) -> list[Person]:
        """Return all of one org's people, newest-first."""
        result = await self._session.execute(
            select(Person)
            .where(Person.org_id == org_id)
            .order_by(Person.created_at.desc(), Person.id.desc())
        )
        return list(result.scalars().all())

    async def get_person_id_by_email(self, org_id: UUID, email: str) -> UUID | None:
        """Return the person owning `email` (NORMALIZED) in this org — the match-key lookup."""
        result = await self._session.execute(
            select(PersonEmail.person_id).where(
                PersonEmail.org_id == org_id, PersonEmail.email == email
            )
        )
        return result.scalar_one_or_none()

    async def add_email(self, person_email: PersonEmail) -> PersonEmail:
        """Attach a normalized email to a person (caller sets org_id/person_id/email)."""
        self._session.add(person_email)
        await self._session.flush()
        return person_email

    async def add_alias(self, alias: PersonAlias) -> PersonAlias:
        """Record an alternate display name for a person."""
        self._session.add(alias)
        await self._session.flush()
        return alias

    async def backfill_display_name(
        self, org_id: UUID, person_id: UUID, display_name: str
    ) -> None:
        """Set display_name ONLY if currently NULL/empty — first non-empty name wins (DQ-K04).

        A no-op once a person already has a name (a later sighting never overwrites a good name),
        so a person first seen as a bare address is repaired when a named sighting arrives.
        """
        await self._session.execute(
            update(Person)
            .where(
                Person.id == person_id,
                Person.org_id == org_id,
                or_(Person.display_name.is_(None), Person.display_name == ""),
            )
            .values(display_name=display_name)
        )

    async def extend_seen_window(self, org_id: UUID, person_id: UUID, seen_at: datetime) -> None:
        """Widen [first_seen_at, last_seen_at] to include `seen_at` — order-independent (LEAST/
        GREATEST in SQL), so emails ingested in any order converge to the true earliest/latest."""
        await self._session.execute(
            update(Person)
            .where(Person.id == person_id, Person.org_id == org_id)
            .values(
                first_seen_at=func.least(func.coalesce(Person.first_seen_at, seen_at), seen_at),
                last_seen_at=func.greatest(func.coalesce(Person.last_seen_at, seen_at), seen_at),
            )
        )
