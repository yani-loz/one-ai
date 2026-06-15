"""
Role: Tier-3 self-connect orchestration (CO-01 /me/connectors) — a user connects, manages, and
      erases THEIR OWN connections after permission resolution + explicit consent. Business logic
      (rule A5). Reuses ConnectorService (encrypt/verify/create) and SyncService (sync) behind a
      per-user ownership gate.
Used by: routes.me_connector_routes; constructed on the caller's TENANT session, plus a GLOBAL
         entitlement reader for the Tier-1 ceiling.
Depends on: ConnectorConnectionRepository (owner-scoped reads), ConnectorService, SyncService,
            policy/override/entitlement repos, ConnectorConsentService, connector_authz (the rule),
            audit_service, schemas, exceptions.
Key invariants:
  - PER-USER ISOLATION (AC3, non-negotiable): every read/act loads via get_for_owner(id, org, user)
    — another user's (or a shared) connection resolves to None -> ConnectionNotFoundError (404),
    even in the same org. A user can NEVER touch a connection that isn't theirs.
  - PERMISSION + CONSENT GATE (AC2/AC5/AC9): self_connect resolves can_user_self_connect FIRST
    (entitlement -> override -> org-wide) and refuses with the friendly 403 BEFORE any credential
    work; consent.accepted must be true (400 otherwise); the consent is recorded same-tx.
  - §7: the owner sees their own params (ConnectionResponse); the connection itself is owner-tagged
    so admins/platform get metadata only elsewhere.
  - DISCONNECT = per-user raw-tier erasure (AC10/§8): deletes the connection (DB-cascades the
    ingested email/attachments/sync state) and withdraws consent (retained as proof). Audited
    connector.disconnected same-tx.
"""

from __future__ import annotations

from uuid import UUID

from app.connectors.enums import AuthMethod, ConnectorType
from app.connectors.exceptions import (
    ConnectionNotFoundError,
    ConnectorAccessDeniedError,
    ConnectorConsentRequiredError,
    ConnectorNotEntitledError,
)
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.repositories.connector_connection_repository import (
    ConnectorConnectionRepository,
)
from app.connectors.repositories.connector_entitlement_repository import (
    ConnectorEntitlementRepository,
)
from app.connectors.repositories.connector_policy_override_repository import (
    ConnectorPolicyOverrideRepository,
)
from app.connectors.repositories.connector_policy_repository import ConnectorPolicyRepository
from app.connectors.schemas.connector_schemas import CreateConnectionRequest
from app.connectors.schemas.me_connector_schemas import (
    AllowedConnectorTypeResponse,
    SelfConnectRequest,
)
from app.connectors.services.connector_authz import (
    ConnectorAccessDecision,
    ConnectorDenialReason,
    resolve_connector_access,
)
from app.connectors.services.connector_consent_service import ConnectorConsentService
from app.connectors.services.connector_service import ConnectorService
from app.connectors.sync.sync_service import SyncService
from app.identity.enums import AuditActorType
from app.identity.principal import Principal
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_CONNECTION = "connector_connection"

# Friendly, plan-safe messages for a denied self-connect (never reveals other orgs' state).
_DENIAL_MESSAGE: dict[ConnectorDenialReason, str] = {
    ConnectorDenialReason.not_entitled: "This connector is not included in your company's plan.",
    ConnectorDenialReason.admin_denied: "Your administrator hasn't enabled this for you.",
    ConnectorDenialReason.org_disabled: "Your administrator hasn't enabled this for you.",
}


class MeConnectorService:
    """Self-connect + manage + erase a user's OWN connections, gated by permission + consent."""

    def __init__(
        self,
        *,
        connections: ConnectorConnectionRepository,
        connector_service: ConnectorService,
        sync_service: SyncService,
        policies: ConnectorPolicyRepository,
        overrides: ConnectorPolicyOverrideRepository,
        entitlements: ConnectorEntitlementRepository,
        consent_service: ConnectorConsentService,
        audit: AuditService,
    ) -> None:
        """Wire the owner-scoped repo, the reused connector/sync services, and the authz inputs."""
        self._connections = connections
        self._connector_service = connector_service
        self._sync_service = sync_service
        self._policies = policies
        self._overrides = overrides
        self._entitlements = entitlements
        self._consent_service = consent_service
        self._audit = audit

    async def list_allowed_types(
        self, org_id: UUID, user_id: UUID
    ) -> list[AllowedConnectorTypeResponse]:
        """Return, per connector type, whether this user may self-connect it (panel cards).

        Phase 1 ships IMAP only; a denied/non-entitled type comes back allowed=False with a friendly
        reason so the UI can lock the card instead of offering it.
        """
        results: list[AllowedConnectorTypeResponse] = []
        for connector_type in ConnectorType:
            decision = await self._resolve(org_id, user_id, connector_type)
            results.append(
                AllowedConnectorTypeResponse(
                    connector_type=connector_type.value,
                    allowed=decision.allowed,
                    reason=_reason_for(decision),
                )
            )
        return results

    async def list_my_connections(self, org_id: UUID, user_id: UUID) -> list[ConnectorConnection]:
        """Return all of the calling user's OWN connections, newest-first."""
        return await self._connections.list_for_owner(org_id, user_id)

    async def get_my_connection(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> ConnectorConnection:
        """Load one of the user's OWN connections, or 404 (per-user isolation)."""
        connection = await self._connections.get_for_owner(connection_id, org_id, user_id)
        if connection is None:
            raise ConnectionNotFoundError("Connection not found.")
        return connection

    async def self_connect(
        self, org_id: UUID, request: SelfConnectRequest, actor: Principal
    ) -> ConnectorConnection:
        """Connect the user's OWN mailbox after permission + consent gates (AC2/AC5/AC9).

        Resolves can_user_self_connect first (refuses with the friendly 403 before any credential
        work), requires an accepted consent (400 otherwise), creates the owner-scoped connection
        (credential encrypted), records the consent, and audits connector.connected — all same-tx.
        """
        user_id = actor.subject_id
        decision = await self._resolve(org_id, user_id, request.connector_type)
        if not decision.allowed:
            _raise_denied(decision)
        if not request.consent.accepted:
            raise ConnectorConsentRequiredError("Consent is required to connect a mailbox.")

        create_request = CreateConnectionRequest(
            connector_type=request.connector_type,
            display_name=request.display_name,
            host=request.host,
            port=request.port,
            use_ssl=request.use_ssl,
            username=request.username,
            password=request.password,
        )
        connection = await self._connector_service.create_connection(
            org_id,
            create_request,
            actor,
            owner_user_id=user_id,
            audit_action=AuditAction.CONNECTOR_CONNECTED,
        )
        await self._consent_service.record(
            org_id=org_id,
            connector_type=request.connector_type.value,
            scope=request.consent.scope,
            method=AuthMethod.app_password.value,
            consent_version=request.consent.consent_version,
            actor=actor,
        )
        return connection

    async def test_my_connection(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> ConnectorConnection:
        """Verify the user's OWN connection (404 if not theirs), if still allowed to use it.

        Re-resolves CURRENT access first (a since-denied/since-revoked user can't keep using the
        connector — only disconnect/erase). Owner-scoped reuse of the verifier.
        """
        connection = await self.get_my_connection(org_id, user_id, connection_id)
        await self._require_current_access(org_id, user_id, connection.connector_type)
        return await self._connector_service.test_connection(
            org_id, connection_id, owner_user_id=user_id
        )

    async def sync_my_connection(
        self, org_id: UUID, user_id: UUID, connection_id: UUID, actor: Principal
    ) -> ConnectorConnection:
        """Trigger a sync of the user's OWN connection (404 if not theirs), if still allowed.

        Re-resolves CURRENT access BEFORE syncing (CO-01 / cross-vendor review): a user allowed at
        connect time must STOP ingesting once an admin sets a deny override or the platform revokes
        the entitlement — else a now-denied user keeps pulling mailbox data. Disconnect/erase remain
        allowed. Owner-scoped reuse of SyncService.
        """
        connection = await self.get_my_connection(org_id, user_id, connection_id)
        await self._require_current_access(org_id, user_id, connection.connector_type)
        return await self._sync_service.start_sync(
            org_id, connection_id, actor=actor, owner_user_id=user_id
        )

    async def _require_current_access(
        self, org_id: UUID, user_id: UUID, connector_type_value: str
    ) -> None:
        """Raise the friendly 403 unless the user CURRENTLY may use the type (entitlement->override
        ->org-wide). Used by sync/test so a since-revoked grant stops active connector use."""
        decision = await self._resolve(org_id, user_id, ConnectorType(connector_type_value))
        if not decision.allowed:
            _raise_denied(decision)

    async def get_my_sync_status(
        self, org_id: UUID, user_id: UUID, connection_id: UUID
    ) -> ConnectorConnection:
        """Poll the live sync progress of the user's OWN connection (404 if not theirs)."""
        return await self._sync_service.get_sync_status(
            org_id, connection_id, owner_user_id=user_id
        )

    async def disconnect_my_connection(
        self, org_id: UUID, user_id: UUID, connection_id: UUID, actor: Principal
    ) -> None:
        """Disconnect + raw-tier erase the user's OWN connection (AC10/§8), audited same-tx.

        Withdraws consent (retained as proof), audits connector.disconnected, and deletes the
        connection — the DB cascade removes the ingested email/attachments/sync state (the raw
        personal-data tier). The learned/derived company knowledge is untouched (§8 / §10.4).
        404 if the connection isn't the user's.
        """
        connection = await self.get_my_connection(org_id, user_id, connection_id)
        # Consent is type-keyed, but a user may own several mailboxes of one type. Withdraw the
        # type-level consent ONLY when this is the user's LAST connection of the type — otherwise a
        # remaining mailbox would be left without an active lawful basis (§8 / Art. 7). Checked
        # BEFORE the delete so the just-removed row can't be miscounted.
        others_of_type = [
            other
            for other in await self._connections.list_for_owner(org_id, user_id)
            if other.connector_type == connection.connector_type and other.id != connection_id
        ]
        if not others_of_type:
            await self._consent_service.withdraw(
                org_id=org_id, connector_type=connection.connector_type, actor=actor
            )
        await self._audit.record(
            AuditEvent(
                action=AuditAction.CONNECTOR_DISCONNECTED,
                actor_type=AuditActorType(actor.subject_type),
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_CONNECTION,
                entity_id=connection.id,
                details={"connector_type": connection.connector_type},
            )
        )
        await self._connections.delete(connection)

    async def _resolve(
        self, org_id: UUID, user_id: UUID, connector_type: ConnectorType
    ) -> ConnectorAccessDecision:
        """Fetch the three inputs (entitlement/override/org-wide) and resolve the decision."""
        entitled = await self._entitlements.is_entitled(org_id, connector_type.value)
        override = await self._overrides.get_override_type(org_id, user_id, connector_type.value)
        org_wide_enabled = await self._policies.is_org_wide_enabled(org_id, connector_type.value)
        return resolve_connector_access(
            entitled=entitled, override=override, org_wide_enabled=org_wide_enabled
        )


def _reason_for(decision: ConnectorAccessDecision) -> str | None:
    """The friendly denial message for a decision, or None when it's allowed."""
    if decision.allowed or decision.denial_reason is None:
        return None
    return _DENIAL_MESSAGE.get(decision.denial_reason)


def _raise_denied(decision: ConnectorAccessDecision) -> None:
    """Map a denial decision to the right friendly 403 (not-entitled vs admin-hasn't-enabled)."""
    message = _reason_for(decision) or "You can't connect this connector."
    if decision.denial_reason is ConnectorDenialReason.not_entitled:
        raise ConnectorNotEntitledError(message)
    raise ConnectorAccessDeniedError(message)
