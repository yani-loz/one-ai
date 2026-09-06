"""
Role: The single choke point where retrieval grants come to exist (PF-01 §5): captures WHO may
      retrieve one ingested content object, resolving principals ONLY through verified
      principal_source_identity rows. The two named conservative rules live here and nowhere
      else: UNKNOWN ⇒ DENY (an unmappable/unverified principal writes NO grant, AC8) and
      SHRINK ⇒ TOMBSTONE (reconciliation revokes grants for principals no longer derivable,
      AC21 — grant writing is reconciliation, never append-only).
Used by: app.connectors.imap.services.email_ingest_service (per-message email capture at ingest);
      each future connector's ingest calls the same writer.
Depends on: .repositories (acl_grant + source_identity), app.entities.services.email_normalizer
      (the ONE normalization path — a grant lookup must normalize exactly like the graph).
Key invariants:
  - Grants resolve through the SECURITY identity table (verified rows), NEVER through the entity
    resolver's auto-minted person graph — content identity must not authorize (AC8/AC20).
  - Email capture granularity is PER-MESSAGE (epic §6): one live grant row per (message,
    principal); the owner resolves via the 'auth' namespace (external_id = str(owner_user_id)),
    participants via the 'email' namespace (external_id = normalized address).
  - Idempotent: re-ingesting/re-seeing a message re-derives the same grants (ON CONFLICT no-op);
    reconcile_email_message_grants() then tombstones any principal that dropped out.
  - The caller owns the transaction — grants commit or roll back WITH the content row.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import GRANT_OBJECT_TYPES
from app.access.repositories.acl_grant_repository import AclGrantRepository
from app.access.repositories.source_identity_repository import SourceIdentityRepository
from app.entities.services.email_normalizer import normalize_email

_EMAIL_OBJECT_TYPE = "email_message"
assert _EMAIL_OBJECT_TYPE in GRANT_OBJECT_TYPES


class GrantWriter:
    """Derives + reconciles acl_grant rows for ingested content (UNKNOWN ⇒ DENY inside)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant session (the ingest transaction)."""
        self._grants = AclGrantRepository(session)
        self._identities = SourceIdentityRepository(session)

    # Provenance precedence when one person appears in several roles (e.g. two email identities
    # bound to the same person, or the owner also being a recipient): owner > sender > recipient.
    _PROVENANCE_RANK: dict[str, int] = {"recipient": 0, "sender": 1, "owner": 2}

    async def write_email_grants(
        self,
        org_id: UUID,
        message_id: UUID,
        connection_id: UUID,
        owner_user_id: UUID | None,
        sender_address: str | None,
        disclosed_recipient_addresses: list[str],
    ) -> set[UUID]:
        """Write the per-message grants for one stored email; returns the granted principal set.

        Principal derivation (all through VERIFIED identities — UNKNOWN ⇒ DENY):
          - the connection owner: 'auth' namespace binding of owner_user_id ('owner');
          - the From author: 'email' namespace binding of the normalized address ('sender');
          - every DISCLOSED recipient (to/cc ONLY — see DISCLOSED_RECIPIENT_KINDS): 'recipient'.
        Bcc/Reply-To/Sender are excluded: they are forgeable on delivered mail AND absent from
        the dedup key, so deriving from them let two copies of one message disagree about who
        may read it, and ingest ORDER decided the winner.
        One grant per principal, provenance by the owner > sender > recipient precedence.
        Unresolvable addresses simply write nothing: an org member who was not a recipient ends
        with no grant and sees nothing (AC10).
        """
        principals = await self._derive_email_principal_roles(
            org_id, owner_user_id, sender_address, disclosed_recipient_addresses
        )
        for person_id, provenance in principals.items():
            await self._grants.insert_live_grant(
                org_id,
                person_id,
                _EMAIL_OBJECT_TYPE,
                message_id,
                provenance,
                connection_id,
            )
        return set(principals)

    async def reconcile_email_message_grants(
        self,
        org_id: UUID,
        message_id: UUID,
        connection_id: UUID,
        owner_user_id: UUID | None,
        sender_address: str | None,
        disclosed_recipient_addresses: list[str],
    ) -> int:
        """Re-derive one message's principal set and TOMBSTONE grants that dropped out (AC21).

        SHRINK ⇒ TOMBSTONE: a principal in the live grant set but no longer derivable (identity
        unverified/unbound, recipient set corrected) is revoked — access removal propagates
        WITHOUT requiring object deletion. Also adds any newly-derivable grants (a fresh
        verified binding starts matching on the next pass — the ingest skip path calls this on
        every re-seen message, making re-ingest the grant backfill). Returns the number of
        tombstoned grants. Principals are derived ONCE (write_email_grants returns the set).
        """
        derivable = await self.write_email_grants(
            org_id,
            message_id,
            connection_id,
            owner_user_id,
            sender_address,
            disclosed_recipient_addresses,
        )
        live = await self._grants.list_live_person_ids_for_object(
            org_id, _EMAIL_OBJECT_TYPE, message_id
        )
        return await self._grants.tombstone_grants(
            org_id, _EMAIL_OBJECT_TYPE, message_id, live - derivable
        )

    async def tombstone_grants_for_person(self, org_id: UUID, person_id: UUID) -> int:
        """Revoke ALL of one principal's live grants (identity binding removed/unverified)."""
        return await self._grants.tombstone_grants_for_person(org_id, person_id)

    async def _derive_email_principal_roles(
        self,
        org_id: UUID,
        owner_user_id: UUID | None,
        sender_address: str | None,
        disclosed_recipient_addresses: list[str],
    ) -> dict[UUID, str]:
        """The CURRENTLY-derivable principal → provenance map (owner > sender > recipient)."""
        address_roles: dict[str, str] = {}
        for address in disclosed_recipient_addresses:
            normalized = normalize_email(address)
            if normalized:
                address_roles.setdefault(normalized, "recipient")
        if sender_address:
            normalized_sender = normalize_email(sender_address)
            if normalized_sender:
                address_roles[normalized_sender] = "sender"

        principals: dict[UUID, str] = {}
        resolved = await self._identities.get_verified_person_ids(
            org_id, "email", address_roles.keys()
        )
        for normalized, person_id in resolved.items():
            role = address_roles[normalized]
            held = principals.get(person_id)
            if held is None or self._PROVENANCE_RANK[role] > self._PROVENANCE_RANK[held]:
                principals[person_id] = role

        if owner_user_id is not None:
            owner_person_id = await self._identities.get_verified_person_id(
                org_id, "auth", str(owner_user_id)
            )
            if owner_person_id is not None:
                principals[owner_person_id] = "owner"
        return principals
