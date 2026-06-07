"""
Role: Entities module — the shared, cross-source identity Core: persons and companies, plus the
      deterministic resolver (later) that merges the same human/company across every connector.
Used by: connectors (Layer-1 source tables reference person/company; the resolver links addresses
         to entities). NEVER imported by app.core.
Depends on: app.common (Base/mixins), app.core (db/exceptions). Connector-agnostic — depends on no
            connector.
Key invariants:
  - Tenant-scoped: every entity carries org_id (each tenant has its OWN graph).
  - Email is the deterministic MATCH KEY (normalized), not a primary key — one person, many emails.
"""
