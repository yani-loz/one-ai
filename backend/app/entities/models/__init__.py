"""
Role: Entity model package — imports every entity ORM model so it registers on Base.metadata.
Used by: app.db.migrations.env (target metadata), entities repositories, the standing RLS-invariant
         test, and the test schema fixtures.
Depends on: app.entities.models.person, app.entities.models.company.
Key invariants:
  - Importing this package registers EVERY entity table on Base.metadata — relied on by
    db.migrations.env (autogenerate), the RLS-invariant test (which enumerates TenantMixin
    subclasses and would miss a table whose model isn't imported), and the test schema fixtures.
"""

from __future__ import annotations

from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail

__all__ = [
    "Company",
    "CompanyDomain",
    "Person",
    "PersonAlias",
    "PersonCompany",
    "PersonEmail",
]
