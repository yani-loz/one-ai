"""
Role: The deterministic entity-resolution backbone (design §6) — turns an email participant
      (address + optional name) into a Person, get-or-creating the Person, its Company (by domain),
      and the person↔company link, all org-scoped. v1 is the DETERMINISTIC tier only: exact
      normalized-email match → link/create. The ambiguous "pit" + HITL + name-merge tiers are
      deferred (they need provenance/confidence columns not in the 3a schema).
Used by: the IMAP ingest runner (step 3d) — once per sender/recipient — to fill
         email_message.from_person_id / email_recipient.person_id.
Depends on: app.entities.repositories (PersonRepository, CompanyRepository), the .models, and the
            pure helpers .email_normalizer + .address_rules.
Key invariants:
  - The email match key is normalized through email_normalizer.normalize_email on the ONE path that
    both looks up and inserts — never two different normalizations (a mismatch silently fails to
    match). The raw as-seen address stays on the source rows; only the key is normalized.
  - EXCLUSION guards (the only resolution rules in v1): a role/shared mailbox (`info@`, `kontakt@`)
    never becomes a Person (returns None); a generic free-mail domain (`gmail.com`) never becomes a
    Company. Over-exclusion only under-creates (a recoverable fragment), never over-merges.
  - get-or-create is race-safe: a concurrent insert that wins the UNIQUE(org_id,email|domain) is
    caught at a SAVEPOINT and resolved by re-reading the winner — so parallel ingest is correct.
  - Provenance: person_email.source records the originating connector. Email-key links are gospel.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonEmail
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
    ) -> UUID | None:
        """Resolve one address to a person_id, get-or-creating person + company + link.

        Returns None when the address is not a person — an empty/garbage address (no '@', or an
        empty local-part/domain like `@x.com` / `a@`) or a role/shared mailbox (design §5:
        `info@`/`noreply@` are not people). The address still lives as-seen on the source row; it
        just earns no entry in the person graph.
        """
        normalized = normalize_email(raw_address)
        local_part, _, domain = normalized.rpartition("@")
        if not local_part or not domain or is_role_address(normalized):
            return None

        person_id = await self._get_or_create_person(org_id, normalized, display_name)
        if seen_at is not None:
            await self._people.extend_seen_window(org_id, person_id, seen_at)
        await self._resolve_company(org_id, person_id, normalized)
        return person_id

    async def _get_or_create_person(
        self, org_id: UUID, normalized_email: str, display_name: str | None
    ) -> UUID:
        """Return the person owning `normalized_email`, creating one if none exists (race-safe)."""
        existing = await self._people.get_person_id_by_email(org_id, normalized_email)
        if existing is not None:
            return existing

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

    async def _resolve_company(self, org_id: UUID, person_id: UUID, normalized_email: str) -> None:
        """Get-or-create the company for the domain and link the person (skips generic domains)."""
        domain = extract_domain(normalized_email)
        if domain is None or is_generic_email_domain(domain):
            return
        company_id = await self._get_or_create_company(org_id, domain)
        await self._link_person_company(org_id, person_id, company_id)

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
                    CompanyDomain(org_id=org_id, company_id=company.id, domain=domain)
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
