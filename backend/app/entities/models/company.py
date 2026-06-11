"""
Role: Company entity (the EXTERNAL organizations a tenant deals with — clients, vendors, partners),
      its domains, and the person↔company link. Companies are matched by email domain.
Used by: entities repositories + (later) the entity resolver; person_company links person↔company.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin).
Key invariants:
  - NAMING: `company` is the EXTERNAL org, NOT the tenant key `org_id`. `org_id` always means the
    tenant; a `company` is a discovered external organization.
  - TENANT-SCOPED (org_id NOT NULL + indexed): each tenant has its OWN companies. Since 0014
    org_id also FKs organizations(id) (no phantom tenants), company anchors UNIQUE (org_id, id),
    and the child FKs are the COMPOSITE (org_id, company_id) form — child.org_id =
    company.org_id is DB-enforced.
  - `company_domain.domain` is the normalized domain match key, UNIQUE(org_id, domain). Generic
    free-mail domains (gmail.com, …) are NOT companies — the resolver skips them.
  - A company may own MANY domains (company.com + company.de) — domain is a match key, not the PK.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Company(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """An external organization the tenant interacts with (NOT the tenant itself)."""

    __tablename__ = "company"
    __table_args__ = (
        # 0014 composite-FK anchor: children FK (org_id, id) so their org_id can never diverge.
        UniqueConstraint("org_id", "id", name="uq_company_org_row"),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_company_org_id"),
    )

    name: Mapped[str | None] = mapped_column(String(320))
    # Marks the tenant's OWN company (vs an external counterparty) — set later by the resolver,
    # mirroring person.is_internal.
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class CompanyDomain(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A normalized email domain owned by a company — the deterministic company match key."""

    __tablename__ = "company_domain"
    __table_args__ = (
        UniqueConstraint("org_id", "domain", name="uq_company_domain_identity"),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_company_domain_org_id"),
        ForeignKeyConstraint(
            ["org_id", "company_id"],
            ["company.org_id", "company.id"],
            ondelete="CASCADE",
            name="fk_company_domain_company_id_org",
        ),
    )

    company_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))


class PersonCompany(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Links a person to a company (e.g. derived from their email domain)."""

    __tablename__ = "person_company"
    __table_args__ = (
        UniqueConstraint("org_id", "person_id", "company_id", name="uq_person_company_identity"),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_person_company_org_id"),
        ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="CASCADE",
            name="fk_person_company_person_id_org",
        ),
        ForeignKeyConstraint(
            ["org_id", "company_id"],
            ["company.org_id", "company.id"],
            ondelete="CASCADE",
            name="fk_person_company_company_id_org",
        ),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    company_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
