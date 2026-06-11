"""
Role: The deterministic entity-resolution backbone (design §6) — turns an email participant
      (address + optional name) into the shared graph, all org-scoped. Company-observation and
      person-creation are DECOUPLED (DQ-D01): a non-generic domain is ALWAYS observed as a Company
      (even a role/no-reply address is evidence the counterparty exists), while a Person is
      created only when the address qualifies. v1 is the DETERMINISTIC tier only: exact normalized-
      email match → link/create. The ambiguous "pit" + HITL + cross-person name-merge tiers are
      deferred (they need provenance/confidence columns not in the 3a schema).
Used by: the IMAP ingest runner (step 3d) — once per sender/recipient — to fill
         email_message.from_person_id / email_recipient.person_id.
Depends on: app.entities.repositories (PersonRepository, CompanyRepository), the .models, and the
            pure helpers .email_normalizer + .address_rules.
Key invariants:
  - The email match key is normalized through email_normalizer.normalize_email on the ONE path that
    both looks up and inserts — never two different normalizations (a mismatch silently fails to
    match). The raw as-seen address stays on the source rows; only the key is normalized.
  - PERSON-HOOD guards: a role/shared mailbox (`info@`, `kontakt@`) never becomes a Person, and the
    caller can suppress person-hood via allow_person=False (DQ-C01 an automated sender; DQ-C02 a
    reply_to/sender-only routing identity). In every case the domain is STILL observed as a Company.
    A generic free-mail domain (`gmail.com`) never becomes a Company. Over-exclusion only
    under-creates (a recoverable fragment), never over-merges.
  - ENRICHMENT (DQ-K04): on every sighting the person's blank display_name is back-filled (first
    non-empty name wins) and a deduped person_alias is recorded — so a person first seen as a bare
    address gains a name later; this is intra-person enrichment, NOT a cross-person merge.
  - get-or-create is race-safe: a concurrent insert that wins the UNIQUE(org_id,email|domain|alias)
    is caught at a SAVEPOINT and resolved by re-reading the winner — so parallel ingest is correct.
  - Provenance: person_email.source / company_domain.source record the originating connector.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.company_repository import CompanyRepository
from app.entities.repositories.person_repository import PersonRepository
from app.entities.services.address_rules import is_generic_email_domain, is_role_address
from app.entities.services.email_normalizer import extract_domain, normalize_email


class EntityResolver:
    """Resolves email participants into the shared person/company graph for one connection."""

    def __init__(self, session: AsyncSession, mailbox_address: str, source: str) -> None:
        """Bind to a tenant session for one connection.

        Args:
            session: the caller's tenant-scoped session (owns the transaction).
            mailbox_address: the synced account address — its (non-generic) domain marks the
                tenant's OWN people/company as internal.
            source: the originating connector type (e.g. 'imap'), recorded as provenance.
        """
        self._session = session
        self._people = PersonRepository(session)
        self._companies = CompanyRepository(session)
        self._mailbox_address = normalize_email(mailbox_address)
        self._mailbox_domain = extract_domain(self._mailbox_address)
        self._source = source

    async def resolve_participant(
        self,
        org_id: UUID,
        raw_address: str,
        display_name: str | None = None,
        seen_at: datetime | None = None,
        *,
        allow_person: bool = True,
    ) -> UUID | None:
        """Resolve one address: observe its Company, and (when eligible) get-or-create its Person.

        Company-observation and person-creation are DECOUPLED (audit DQ-D01): a non-generic domain
        is always observed as a Company — even a role/no-reply/automated address is evidence the
        domain (a counterparty) exists — while a Person is created only when it qualifies.

        Returns the person_id, or None when no Person is created: an empty/garbage address (no '@',
        empty local-part/domain), a role/shared mailbox (`info@`/`noreply@` — design §5), or when
        allow_person=False (DQ-C01: an automated sender; DQ-C02: a reply_to/sender-only routing
        identity). The address still lives as-seen on the source row regardless.
        """
        normalized = normalize_email(raw_address)
        local_part, _, domain = normalized.rpartition("@")
        if not local_part or not domain:
            return None

        # DQ-D01: observe the company for any non-generic domain, independent of person-hood.
        company_id = await self._observe_company(org_id, normalized)

        if not allow_person or is_role_address(normalized):
            return None

        person_id = await self._get_or_create_person(org_id, normalized, display_name)
        if seen_at is not None:
            await self._people.extend_seen_window(org_id, person_id, seen_at)
        if company_id is not None:
            await self._link_person_company(org_id, person_id, company_id)
        return person_id

    async def _get_or_create_person(
        self, org_id: UUID, normalized_email: str, display_name: str | None
    ) -> UUID:
        """Return the person owning `normalized_email`, creating one if none exists (race-safe).

        On EVERY resolution (existing, new, or race-winner) the person is ENRICHED with the current
        sighting's name (DQ-K04): a blank display_name is back-filled and a deduped alias is
        recorded, so a person first seen as a bare address gains a name from a later sighting.
        """
        person_id = await self._people.get_person_id_by_email(org_id, normalized_email)
        if person_id is None:
            person_id = await self._insert_person(org_id, normalized_email, display_name)
        await self._enrich_person(org_id, person_id, display_name)
        return person_id

    async def _insert_person(
        self, org_id: UUID, normalized_email: str, display_name: str | None
    ) -> UUID:
        """Insert a new person + its email key; on a concurrent-insert race, return the winner."""
        domain = extract_domain(normalized_email)
        try:
            async with self._session.begin_nested():
                person = await self._people.insert(
                    Person(
                        org_id=org_id,
                        display_name=display_name,
                        is_internal=self._is_internal(normalized_email, domain),
                    )
                )
                await self._people.add_email(
                    PersonEmail(
                        org_id=org_id,
                        person_id=person.id,
                        email=normalized_email,
                        source=self._source,
                    )
                )
            return person.id
        except IntegrityError:
            # A concurrent ingest inserted the same email first — re-read the winner.
            winner = await self._people.get_person_id_by_email(org_id, normalized_email)
            if winner is None:  # pragma: no cover - the UNIQUE guarantees a winner exists
                raise
            return winner

    async def _enrich_person(
        self, org_id: UUID, person_id: UUID, display_name: str | None
    ) -> None:
        """DQ-K04: back-fill a blank display_name from this sighting + record a deduped alias."""
        name = (display_name or "").strip()
        if not name:
            return
        await self._people.backfill_display_name(org_id, person_id, name)
        try:
            async with self._session.begin_nested():
                await self._people.add_alias(
                    PersonAlias(
                        org_id=org_id, person_id=person_id, alias=name, source=self._source
                    )
                )
        except IntegrityError:
            pass  # this exact alias is already recorded for the person (UNIQUE) — idempotent

    async def _observe_company(self, org_id: UUID, normalized_email: str) -> UUID | None:
        """Get-or-create the Company for the address's domain; return its id (None for generic).

        Observation is independent of person-hood (DQ-D01) — the caller links the person separately,
        only when one is created.
        """
        domain = extract_domain(normalized_email)
        if domain is None or is_generic_email_domain(domain):
            return None
        return await self._get_or_create_company(org_id, domain)

    async def _get_or_create_company(self, org_id: UUID, domain: str) -> UUID:
        """Return the company owning `domain`, creating one if none exists (race-safe)."""
        existing = await self._companies.get_company_id_by_domain(org_id, domain)
        if existing is not None:
            return existing

        is_internal = self._mailbox_domain is not None and domain == self._mailbox_domain
        try:
            async with self._session.begin_nested():
                company = await self._companies.insert(
                    Company(org_id=org_id, name=domain, is_internal=is_internal)
                )
                await self._companies.add_domain(
                    CompanyDomain(
                        org_id=org_id, company_id=company.id, domain=domain, source=self._source
                    )
                )
            return company.id
        except IntegrityError:
            winner = await self._companies.get_company_id_by_domain(org_id, domain)
            if winner is None:  # pragma: no cover - the UNIQUE guarantees a winner exists
                raise
            return winner

    async def _link_person_company(
        self, org_id: UUID, person_id: UUID, company_id: UUID
    ) -> None:
        """Idempotently link a person to a company (UNIQUE(org_id, person_id, company_id))."""
        try:
            async with self._session.begin_nested():
                await self._companies.link_person(
                    PersonCompany(org_id=org_id, person_id=person_id, company_id=company_id)
                )
        except IntegrityError:
            pass  # link already exists — get-or-create is idempotent

    def _is_internal(self, normalized_email: str, domain: str | None) -> bool:
        """True if this address belongs to the tenant itself (own-domain colleague, or the mailbox).

        Uses the mailbox's domain when it is a real company domain; for a mailbox on a generic
        provider only the exact mailbox address counts as internal (a shared free-mail domain says
        nothing about who is a colleague).
        """
        if self._mailbox_domain and not is_generic_email_domain(self._mailbox_domain):
            return domain == self._mailbox_domain
        return normalized_email == self._mailbox_address
