"""
Role: Tier-3 consent orchestration (CO-01 §8) — record a user's GDPR Art. 7 consent at
      self-connect and withdraw it on disconnect/erasure. Business logic (rule A5).
Used by: me_connector_service (capture at connect, withdraw at disconnect); constructed on the
         caller's TENANT session.
Depends on: ConnectorConsentRepository, identity.services.audit_service, identity Principal/
            AuditActorType, the ConnectorConsent model.
Key invariants:
  - Consent is the user's OWN: the user_id is the verified principal; only the owner self-connects.
  - WITHDRAWAL is a mark, not a delete (Art. 7(4)) — the row is retained as proof of the lawful
    basis that existed. Audited as connector.consented / connector.consent_withdrawn same-tx.
  - ui_proof holds NON-PII proof only (consent version + accepted flag) — never an IP or content.
"""

from __future__ import annotations

from uuid import UUID

from app.connectors.models.connector_consent import ConnectorConsent
from app.connectors.repositories.connector_consent_repository import ConnectorConsentRepository
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_CONSENT = "connector_consent"


class ConnectorConsentService:
    """Capture + withdraw per-user connector consent (GDPR Art. 7), audited."""

    def __init__(self, consents: ConnectorConsentRepository, audit: AuditService) -> None:
        """Wire the consent repository (tenant session) and the audit writer."""
        self._consents = consents
        self._audit = audit

    async def record(
        self,
        *,
        org_id: UUID,
        connector_type: str,
        scope: str,
        method: str,
        consent_version: str,
        actor: Principal,
    ) -> ConnectorConsent:
        """Record the calling user's consent for a type at self-connect, audited same-tx.

        ui_proof captures the non-PII proof (consent version + accepted flag). The actor IS the
        consenting user (verified principal).
        """
        consent = ConnectorConsent(
            org_id=org_id,
            user_id=actor.subject_id,
            connector_type=connector_type,
            scope=scope,
            method=method,
            ui_proof={"consent_version": consent_version, "accepted": True},
        )
        await self._consents.insert(consent)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.CONNECTOR_CONSENTED,
                actor_type=AuditActorType(actor.subject_type),
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_CONSENT,
                entity_id=consent.id,
                details={"connector_type": connector_type, "scope": scope, "method": method},
            )
        )
        return consent

    async def withdraw(self, *, org_id: UUID, connector_type: str, actor: Principal) -> None:
        """Withdraw the calling user's in-force consent(s) for a type (Art. 7(4)), audited same-tx.

        The rows are retained as proof — withdrawal is a mark, not a delete. Records
        connector.consent_withdrawn only when a consent was actually in force (no-op stays silent).
        """
        withdrawn = await self._consents.withdraw_active(org_id, actor.subject_id, connector_type)
        if withdrawn > 0:
            await self._audit.record(
                AuditEvent(
                    action=AuditAction.CONNECTOR_CONSENT_WITHDRAWN,
                    actor_type=AuditActorType(actor.subject_type),
                    actor_id=actor.subject_id,
                    org_id=org_id,
                    entity_type=_ENTITY_CONSENT,
                    details={"connector_type": connector_type},
                )
            )
