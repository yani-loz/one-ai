"""
Role: The Connect module's GDPR org-erasure step — deletes ALL of one org's connector rows
      (connector_connection incl. the mailbox username + encrypted IMAP credential, the sync
      ledger/cursors, and the Layer-1 email tables) with explicit org-scoped DELETEs, returning
      honest per-table counts for the deletion certificate (CA-CONN-01 + CA-CONN-03).
Used by: app.main (registered on the erasure-hook registry at startup); ErasureService runs it
         inside the erasure transaction via app.common.erasure_hooks.
Depends on: app.connectors.models (connector_connection / sync run / sync cursor),
            app.connectors.imap.models.email (the Layer-1 email tables), SQLAlchemy async.
Key invariants:
  - RUNS ON THE RLS-EXEMPT GLOBAL SESSION (erasure is a platform flow): EVERY DELETE here is
    org-scoped in its own WHERE clause — that org_id filter is the ONLY containment between
    "erase org A" and "erase everything". Never add an unscoped statement.
  - EXPLICIT children-first deletes, deliberately NOT relying on the DB FK CASCADEs
    (email_* / sync_* do cascade from connector_connection — migrations 0008/0011 — but
    explicit per-table deletes give exact per-table counts, so the certificate never lies).
  - The caller owns the transaction (ErasureService's session); this only executes deletes,
    never commits.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun

# Children before parents (email_* FK email_message FK connector_connection; sync_* FK
# connector_connection), so the explicit deletes never trip an FK even without CASCADE.
_CONNECT_TABLES_CHILDREN_FIRST = (
    EmailRecipient,
    EmailAttachment,
    EmailMessage,
    ConnectorSyncCursor,
    ConnectorSyncRun,
    ConnectorConnection,
)


async def erase_connector_data_for_org(org_id: UUID, session: AsyncSession) -> dict[str, int]:
    """Hard-delete every Connect row belonging to `org_id`; return {table_name: rows_deleted}.

    The Connect erasure hook (registered in app.main). Deletes, children-first:
    email_recipient, email_attachment, email_message, connector_sync_cursor,
    connector_sync_run, connector_connection. Each DELETE filters by org_id — the sole
    containment on the RLS-exempt session (see module invariants). Idempotent: an org with
    no Connect data returns all-zero counts.
    """
    rows_deleted: dict[str, int] = {}
    for model in _CONNECT_TABLES_CHILDREN_FIRST:
        result = await session.execute(delete(model).where(model.org_id == org_id))
        rows_deleted[model.__tablename__] = result.rowcount or 0
    return rows_deleted
