"""
Role: GDPR erasure for the access module's four tables (PF-01 AC15) — explicit org-scoped
      DELETEs, children-first, feeding the erasure certificate's per-table counts.
Used by: app.main (create_app registers it on the erasure-hook registry as 'access' — `identity`
      imports NOTHING from here, the registry inverts the dependency).
Depends on: app.access.models.
Key invariants:
  - Runs INSIDE the erasure transaction on the RLS-exempt global session: every statement
    carries its own WHERE org_id — that clause IS the containment (audit-fix-pass convention).
  - Scrub-vs-retain per the epic §6: acl_grant DELETE (pure access metadata),
    principal_source_identity DELETE (external identifiers are PII), visibility_promotion
    DELETE the mutable rows (the lineage survives in the RETAINED append-only audit_log —
    Art. 17(3) basis), fact_provenance DELETE (dies with the facts it anchors).
  - Registered FIRST (before connectors/entities): explicit grant/identity deletes must precede
    the person/connection deletes that would otherwise cascade them invisibly, keeping the
    certificate counts honest.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.access.models.fact_provenance import FactProvenance
from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.models.visibility_promotion import VisibilityPromotion

# No FKs between these four; order is stable-alphabetical-by-dependency for a deterministic
# certificate. All reference person/users/connections — which is why 'access' registers FIRST.
_ACCESS_TABLES = (AclGrant, FactProvenance, VisibilityPromotion, PrincipalSourceIdentity)


async def erase_access_data_for_org(org_id: UUID, session: AsyncSession) -> dict[str, int]:
    """Delete every access-module row of `org_id`; returns per-table deleted counts."""
    rows_deleted: dict[str, int] = {}
    for model in _ACCESS_TABLES:
        result = await session.execute(delete(model).where(model.org_id == org_id))
        rows_deleted[model.__tablename__] = result.rowcount or 0
    return rows_deleted
