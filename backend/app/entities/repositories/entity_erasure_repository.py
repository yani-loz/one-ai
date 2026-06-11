"""
Role: The entities module's GDPR org-erasure step — deletes ALL of one org's entity graph
      (person / person_email / person_alias / company / company_domain / person_company,
      the densest PII in the system) with explicit org-scoped DELETEs, returning honest
      per-table counts for the deletion certificate (CA-CONN-03).
Used by: app.main (registered on the erasure-hook registry at startup); ErasureService runs it
         inside the erasure transaction via app.common.erasure_hooks.
Depends on: app.entities.models.person / company, SQLAlchemy async.
Key invariants:
  - RUNS ON THE RLS-EXEMPT GLOBAL SESSION (erasure is a platform flow): EVERY DELETE here is
    org-scoped in its own WHERE clause — that org_id filter is the ONLY containment between
    "erase org A" and "erase everything". Never add an unscoped statement.
  - EXPLICIT children-first deletes (link/key tables before person/company) for exact
    per-table counts — the certificate never lies. The entity graph does NOT cascade from any
    connector table, so this hook is the only thing that erases it.
  - Register AFTER the connectors hook (app.main order): email_message/email_recipient
    reference person via ON DELETE SET NULL, so deleting email rows first avoids pointless
    SET NULL churn (either order stays correct).
  - The caller owns the transaction (ErasureService's session); this only executes deletes,
    never commits.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail

# Children before parents (person_company FK person+company; company_domain FK company;
# person_email / person_alias FK person), so explicit deletes never trip an FK.
_ENTITY_TABLES_CHILDREN_FIRST = (
    PersonCompany,
    CompanyDomain,
    PersonAlias,
    PersonEmail,
    Company,
    Person,
)


async def erase_entity_graph_for_org(org_id: UUID, session: AsyncSession) -> dict[str, int]:
    """Hard-delete every entity-graph row belonging to `org_id`; return {table: rows_deleted}.

    The entities erasure hook (registered in app.main). Deletes, children-first:
    person_company, company_domain, person_alias, person_email, company, person. Each DELETE
    filters by org_id — the sole containment on the RLS-exempt session (see module
    invariants). Idempotent: an org with no entity graph returns all-zero counts.
    """
    rows_deleted: dict[str, int] = {}
    for model in _ENTITY_TABLES_CHILDREN_FIRST:
        result = await session.execute(delete(model).where(model.org_id == org_id))
        rows_deleted[model.__tablename__] = result.rowcount or 0
    return rows_deleted
