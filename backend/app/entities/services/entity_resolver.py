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
  - The resolver is the NORMALIZATION SEAM for display names (audit H-3): ONE layer of wrapping
    quotes (Outlook's `'Lozanov, Yani'` convention — single or double) is stripped, then
    whitespace re-trimmed, before the name reaches insert, backfill, AND alias paths.
    email_recipient.name stays raw-as-seen BY CONTRACT — the parser never normalizes.
  - PERSON-HOOD guards: a role/shared mailbox (`info@`, `kontakt@`, compound automation localparts
    like `drive-shares-dm-noreply@` — token-matched, audit H-4) never becomes a Person, and the
    caller can suppress person-hood via allow_person=False (DQ-C01 an automated sender; DQ-C02 a
    reply_to/sender-only routing identity). In every case the domain is STILL observed as a Company.
    A generic free-mail domain (`gmail.com`, `abv.bg`) never becomes a Company. Over-exclusion only
    under-creates (a recoverable fragment), never over-merges.
  - COMPANY IDENTITY folds to the eTLD+1 registrable domain (audit M-9): `bg.ibm.com` and
    `ibm.com` resolve to ONE company keyed `ibm.com`, while every distinct observed host is still
    recorded as a company_domain evidence row. PSL-private SaaS suffixes stay distinct identities
    (`foo.atlassian.net` ≠ `bar.atlassian.net`).
  - IDN/PUNYCODE QUARANTINE (audit M-8): a domain with any `xn--` label (homoglyph spoof surface)
    never mints a Company or person_company link — the person still resolves; the sighting is
    logged for future HiTL review. No automatic merging with an ASCII lookalike, ever.
  - ENRICHMENT (DQ-K04): on every sighting the person's blank display_name is back-filled (first
    non-empty name wins) and a deduped person_alias is recorded — so a person first seen as a bare
    address gains a name later; this is intra-person enrichment, NOT a cross-person merge.
  - get-or-create is race-safe: a concurrent insert that wins the UNIQUE(org_id,email|domain|alias)
    is caught at a SAVEPOINT and resolved by re-reading the winner — so parallel ingest is correct.
  - PER-RUN CACHE (perf lane #2): a ResolutionCache memoizes resolved ids + recorded facts so a
    participant seen ~20x per run costs one full resolution, not twenty (measured 2026-07-03: the
    resolver was ~a third of DB round-trip time). The cache is transaction-aware — entries stage
    per email-transaction and confirm only on commit (rollback discards them), so a person minted
    in a failed email can never leak a dangling id into later emails. The race-safe get-or-create
    stays untouched underneath; the cache only skips repeats WITHIN this run/process.
  - Provenance: person_email.source / company_domain.source record the originating connector.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.entities.repositories.company_repository import CompanyRepository
from app.entities.repositories.person_repository import PersonRepository
from app.entities.services.address_rules import (
    fold_to_registrable_domain,
    is_generic_email_domain,
    is_role_address,
    is_suspicious_idn_domain,
)
from app.entities.services.email_normalizer import extract_domain, normalize_email
from app.entities.services.resolution_cache import ResolutionCache

logger = logging.getLogger(__name__)

# Quote characters Outlook conventionally wraps display names in (audit H-3).
_WRAPPING_QUOTE_CHARS = ("'", '"')


def _clean_display_name(display_name: str | None) -> str | None:
    """Normalize a sighted display name for graph storage (audit H-3) — the resolver-side seam.

    Trims whitespace, strips ONE layer of wrapping quotes (single or double — Outlook's
    `'Lozanov, Yani'` convention), then re-trims. A quote is stripped only when the value starts
    AND ends with the SAME quote char and is longer than 2 chars, so `O'Brien` (apostrophe inside,
    no wrap) survives untouched while `'O'Brien'` unwraps to `O'Brien`. Returns None when nothing
    usable remains. The raw as-seen name stays on email_recipient.name by contract.
    """
    if display_name is None:
        return None
    name = display_name.strip()
    if len(name) > 2 and name[0] == name[-1] and name[0] in _WRAPPING_QUOTE_CHARS:
        name = name[1:-1].strip()
    return name or None


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
        self._cache = ResolutionCache(session)
        self._mailbox_address = normalize_email(mailbox_address)
        self._mailbox_domain = extract_domain(self._mailbox_address)
        # The mailbox's COMPANY key (eTLD+1, M-9) — what an observed company key is compared
        # against for internality. None when the mailbox sits on a generic free-mail provider
        # (a shared free-mail domain says nothing about who is a colleague).
        self._mailbox_company_key: str | None = None
        if self._mailbox_domain is not None and not is_generic_email_domain(self._mailbox_domain):
            self._mailbox_company_key = fold_to_registrable_domain(self._mailbox_domain)
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
        if seen_at is not None and not self._cache.seen_window_covers(org_id, person_id, seen_at):
            await self._people.extend_seen_window(org_id, person_id, seen_at)
            self._cache.stage_seen_window(org_id, person_id, seen_at)
        if company_id is not None:
            await self._link_person_company(org_id, person_id, company_id)
        return person_id

    async def _get_or_create_person(
        self, org_id: UUID, normalized_email: str, display_name: str | None
    ) -> UUID:
        """Return the person owning `normalized_email`, creating one if none exists (race-safe).

        The sighted name is cleaned ONCE here (audit H-3: wrapping-quote strip + re-trim) so the
        insert, backfill, and alias paths all store the normalized form. On EVERY resolution
        (existing, new, or race-winner) the person is ENRICHED with the current sighting's name
        (DQ-K04): a blank display_name is back-filled and a deduped alias is recorded, so a person
        first seen as a bare address gains a name from a later sighting.
        """
        cleaned_name = _clean_display_name(display_name)
        person_id = self._cache.person_id(org_id, normalized_email)
        if person_id is None:
            person_id = await self._people.get_person_id_by_email(org_id, normalized_email)
            if person_id is None:
                person_id = await self._insert_person(org_id, normalized_email, cleaned_name)
            self._cache.stage_person(org_id, normalized_email, person_id)
        await self._enrich_person(org_id, person_id, cleaned_name)
        return person_id

    async def _insert_person(
        self, org_id: UUID, normalized_email: str, display_name: str | None
    ) -> UUID:
        """Insert a new person + its email key; on a concurrent-insert race, return the winner.

        `display_name` arrives already cleaned by `_get_or_create_person` (H-3).
        """
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
        self, org_id: UUID, person_id: UUID, cleaned_name: str | None
    ) -> None:
        """DQ-K04: back-fill a blank display_name from this sighting + record a deduped alias.

        `cleaned_name` arrives already normalized by `_get_or_create_person` (H-3 quote-strip) —
        both the backfill and the alias write store the cleaned form. Both writes are skipped when
        this run already ensured them (per-run cache): after ONE backfill with a non-empty name the
        person is definitely named (the UPDATE either found a name or set this one), and after one
        alias write the exact (person, alias) row definitely exists.
        """
        if not cleaned_name:
            return
        if not self._cache.has_person_name(org_id, person_id):
            await self._people.backfill_display_name(org_id, person_id, cleaned_name)
            self._cache.stage_person_name(org_id, person_id)
        if self._cache.has_alias(org_id, person_id, cleaned_name):
            return
        try:
            async with self._session.begin_nested():
                await self._people.add_alias(
                    PersonAlias(
                        org_id=org_id, person_id=person_id, alias=cleaned_name, source=self._source
                    )
                )
        except IntegrityError:
            pass  # this exact alias is already recorded for the person (UNIQUE) — idempotent
        self._cache.stage_alias(org_id, person_id, cleaned_name)

    async def _observe_company(self, org_id: UUID, normalized_email: str) -> UUID | None:
        """Get-or-create the Company for the address's domain; return its id (None when skipped).

        Observation is independent of person-hood (DQ-D01) — the caller links the person separately,
        only when one is created. Skips (returns None): generic free-mail domains, IDN-shaped
        domains in EITHER wire form — `xn--` ACE or raw-Unicode EAI (M-8 quarantine — logged for
        HiTL review, never auto-merged with an ASCII lookalike), and hosts whose eTLD+1 fold lands
        on a generic provider. The company is keyed
        by the registrable domain (M-9); the full observed host is recorded as domain evidence.
        """
        observed_host = extract_domain(normalized_email)
        if observed_host is None or is_generic_email_domain(observed_host):
            return None
        if is_suspicious_idn_domain(observed_host):
            # M-8: spoof-adjacent (homoglyph) surface — quarantine from the company graph.
            # Covers BOTH wire forms: ACE (`xn--`) and raw-Unicode SMTPUTF8/EAI delivery — the
            # same logical spoof domain must not mint a company under either encoding.
            logger.warning(
                "IDN domain quarantined from company minting (needs HiTL review): "
                "org_id=%s domain=%s",
                org_id,
                observed_host,
            )
            return None
        company_key = fold_to_registrable_domain(observed_host)
        if is_generic_email_domain(company_key):
            return None  # a host UNDER a free-mail provider must not resurrect it as a company
        return await self._get_or_create_company(org_id, company_key, observed_host)

    async def _get_or_create_company(
        self, org_id: UUID, company_key: str, observed_host: str
    ) -> UUID:
        """Return the company keyed by `company_key` (eTLD+1), creating one if none exists.

        When the observed host differs from the key (a subdomain sighting), it is additionally
        recorded as an idempotent company_domain evidence row (M-9: key folds, evidence stays
        full-fidelity).
        """
        company_id = self._cache.company_id(org_id, company_key)
        if company_id is None:
            company_id = await self._companies.get_company_id_by_domain(org_id, company_key)
            if company_id is None:
                company_id = await self._insert_company(org_id, company_key)
            self._cache.stage_company(org_id, company_key, company_id)
        if observed_host != company_key:
            await self._record_observed_host(org_id, company_id, observed_host)
        return company_id

    async def _insert_company(self, org_id: UUID, company_key: str) -> UUID:
        """Insert a new company + its key domain row; on a concurrent race, return the winner."""
        is_internal = (
            self._mailbox_company_key is not None and company_key == self._mailbox_company_key
        )
        try:
            async with self._session.begin_nested():
                company = await self._companies.insert(
                    Company(org_id=org_id, name=company_key, is_internal=is_internal)
                )
                await self._companies.add_domain(
                    CompanyDomain(
                        org_id=org_id,
                        company_id=company.id,
                        domain=company_key,
                        source=self._source,
                    )
                )
            return company.id
        except IntegrityError:
            winner = await self._companies.get_company_id_by_domain(org_id, company_key)
            if winner is None:  # pragma: no cover - the UNIQUE guarantees a winner exists
                raise
            return winner

    async def _record_observed_host(
        self, org_id: UUID, company_id: UUID, observed_host: str
    ) -> None:
        """Idempotently record a full observed host as evidence for a folded company (M-9).

        Skipped when this run already ensured the row (per-run cache) — the savepoint round-trip
        for an already-recorded host was the M-9 evidence path's repeat cost.
        """
        if self._cache.has_company_host(org_id, company_id, observed_host):
            return
        try:
            async with self._session.begin_nested():
                await self._companies.add_domain(
                    CompanyDomain(
                        org_id=org_id,
                        company_id=company_id,
                        domain=observed_host,
                        source=self._source,
                    )
                )
        except IntegrityError:
            pass  # this host is already recorded (UNIQUE(org_id, domain)) — idempotent
        self._cache.stage_company_host(org_id, company_id, observed_host)

    async def _link_person_company(
        self, org_id: UUID, person_id: UUID, company_id: UUID
    ) -> None:
        """Idempotently link a person to a company (UNIQUE(org_id, person_id, company_id)).

        Skipped when this run already ensured the link (per-run cache) — this savepoint attempt
        ran once per participant per email before, deliberately failing on the UNIQUE each time.
        """
        if self._cache.has_person_company_link(org_id, person_id, company_id):
            return
        try:
            async with self._session.begin_nested():
                await self._companies.link_person(
                    PersonCompany(org_id=org_id, person_id=person_id, company_id=company_id)
                )
        except IntegrityError:
            pass  # link already exists — get-or-create is idempotent
        self._cache.stage_person_company_link(org_id, person_id, company_id)

    def _is_internal(self, normalized_email: str, domain: str | None) -> bool:
        """True if this address belongs to the tenant itself (own-domain colleague, or the mailbox).

        Compares REGISTRABLE (eTLD+1-folded) domains, consistent with company identity (M-9):
        a mailbox on bg.ibm.com must classify alice@ibm.com as internal — they fold to the same
        company, and an exact-host compare would link her to the tenant's own company yet mark
        her external (2026-06-11 cross-vendor review). PSL-private SaaS suffixes stay distinct
        (tenant-a.onmicrosoft.com is NOT internal to tenant-b.onmicrosoft.com). For a mailbox on
        a generic provider only the exact mailbox address counts as internal (a shared free-mail
        domain says nothing about who is a colleague).
        """
        if self._mailbox_company_key is not None and domain is not None:
            return fold_to_registrable_domain(domain) == self._mailbox_company_key
        return normalized_email == self._mailbox_address
