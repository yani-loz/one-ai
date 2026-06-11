"""data-quality guards — org-root FKs, tenant-coherent composite FKs, identity + ledger CHECKs

Role: Closes the schema gaps confirmed by the 2026-06-10 DB data-quality audit (§4 hardening plan):
      (1) org-root FKs on the 12 undesigned org_id tables so a phantom tenant (H-2) can never be
      minted again; (2) functional UNIQUE indexes on lower(email) for users/platform_admins (M-1);
      (3) composite tenant-coherent FKs — parents email_message/person/company/connector_connection
      gain UNIQUE (org_id, id) and every child FK becomes (org_id, parent_id), so child.org_id =
      parent.org_id is DB-enforced (M-3, L-10); (4) UNIQUE recipient edges (M-6 guard); (5) sync-
      ledger self-defense — run-fencing UNIQUE + time-order/terminal/counts/uidvalidity CHECKs
      (L-2, L-3, L-4); (6) value-shape CHECKs: org slug format (L-1), provenance `source` enums
      (L-5), refresh-token hash shape, attachment filename non-empty (L-8), and the support-grant
      lifecycle (M-2).
Used by: alembic upgrade head (runs as the OWNER role `oneai`, which owns every table).
Depends on: 0013_least_privilege_grants (revision chain); the tables created by 0002 (users/
            organizations/platform_admins/refresh_tokens), 0005 (audit_log), 0006 (support_grant),
            0007 (connector_connection), 0008 (entity graph + email Layer-1), 0011 (sync ledger).
Key invariants:
  - Org-root FKs are ON DELETE NO ACTION (the default), NOT CASCADE: GDPR erasure hooks delete
    tenant children explicitly BEFORE the organizations row, and an accidental org DELETE must
    fail loudly instead of cascading a tenant's corpus away. audit_log + support_grant stay
    FK-free BY DESIGN (durable compliance attribution must survive org deletion — 0005/0006).
  - Each composite child FK preserves its predecessor's EXACT ON DELETE semantics (verified
    against pg_constraint on the live schema): recipient/attachment -> message CASCADE;
    person_email/person_alias/person_company -> person CASCADE; company_domain/person_company ->
    company CASCADE; email_message/sync_run/sync_cursor -> connector_connection CASCADE;
    email_message.from_person_id / email_recipient.person_id -> person SET NULL — the SET NULL
    pair uses PostgreSQL 15+ column-list syntax `ON DELETE SET NULL (<col>)` so only the person
    pointer is nulled, never org_id (which is NOT NULL). The superseded single-column FKs (the
    auto-named `<table>_<column>_fkey` set) are dropped.
  - ck_sync_run_terminal_finished `(status = 'running') = (finished_at IS NULL)` was VERIFIED
    against every ledger writer before adoption: ConnectorSyncRunRepository.start_run opens
    'running' rows with finished_at NULL; finalize() stamps finished_at=now() with
    'succeeded'/'failed'; mark_stale_running_abandoned (the crashed-run reconciliation invoked by
    SyncService.start_sync) stamps 'abandoned' WITH finished_at=now(). No legitimate state
    violates the CHECK — adopted exactly as sketched in the audit.
  - ck_support_grant_lifecycle DIVERGES from the audit's §4 sketch deliberately: the sketch's
    `(status = 'requested') = (decided_at IS NULL)` is violated by the REAL lifecycle —
    CompanySupportService.revoke / PlatformSupportService.revoke transition from 'requested'
    WITHOUT stamping decided_* (only approve/deny call _stamp_decision), so a revoked-from-
    requested row legally keeps decided_at/decided_by_user_id/expires_at all NULL while a
    revoked-from-approved row keeps all three set. The CHECK therefore pins each status to its
    real write-path shape, with 'revoked' allowing both shapes coherently.
  - downgrade() restores the exact 0013 state: every guard dropped and the original single-column
    FKs re-created under their original auto-generated names with their original ON DELETE rules.

PRECONDITIONS (violations make the DDL fail LOUDLY — that is the point):
  - No orphan org_ids: every org_id value in the 12 tables must exist in organizations (the dev
    DB's phantom-tenant corpus is truncated before this applies; scripts/ingest_imap_dump now
    get-or-creates its org row first; production DBs are born at head and can never violate).
  - No duplicate (email_id, kind, address) recipient edges (the M-6 cleanup DELETE has run) and
    no child rows whose org_id differs from their parent's (audit: 0 such rows exist).

Revision ID: 0014_data_quality_guards
Revises: 0013_least_privilege_grants
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op

revision: str = "0014_data_quality_guards"
down_revision: str | None = "0013_least_privilege_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── 1) Org-root FK targets: the 12 undesigned org_id gaps (H-2). users already has
#       fk_users_org_id (0002); audit_log + support_grant stay FK-free BY DESIGN (0005/0006). ──
_ORG_FK_TABLES = [
    "company",
    "company_domain",
    "connector_connection",
    "connector_sync_cursor",
    "connector_sync_run",
    "email_attachment",
    "email_message",
    "email_recipient",
    "person",
    "person_alias",
    "person_company",
    "person_email",
]

# ── 3) Parents that gain UNIQUE (org_id, id) — the composite-FK anchor (cheap: id is the PK). ──
_COMPOSITE_FK_PARENTS = ["email_message", "person", "company", "connector_connection"]


class _CompositeFk(NamedTuple):
    """One child FK to convert from single-column to tenant-coherent composite form.

    `ondelete` is the ORIGINAL single-column FK's exact ON DELETE rule ('CASCADE' | 'SET NULL'),
    read from pg_constraint on the live 0013 schema — preserved by the composite replacement and
    restored verbatim by downgrade().
    """

    child: str
    column: str
    parent: str
    ondelete: str


# Verified against pg_get_constraintdef() output for every existing FK (see module docstring).
_COMPOSITE_FKS = [
    _CompositeFk("email_recipient", "email_id", "email_message", "CASCADE"),
    _CompositeFk("email_attachment", "email_id", "email_message", "CASCADE"),
    _CompositeFk("person_email", "person_id", "person", "CASCADE"),
    _CompositeFk("person_alias", "person_id", "person", "CASCADE"),
    _CompositeFk("person_company", "person_id", "person", "CASCADE"),
    _CompositeFk("person_company", "company_id", "company", "CASCADE"),
    _CompositeFk("company_domain", "company_id", "company", "CASCADE"),
    _CompositeFk("email_message", "connection_id", "connector_connection", "CASCADE"),
    _CompositeFk("connector_sync_run", "connection_id", "connector_connection", "CASCADE"),
    _CompositeFk("connector_sync_cursor", "connection_id", "connector_connection", "CASCADE"),
    _CompositeFk("email_message", "from_person_id", "person", "SET NULL"),
    _CompositeFk("email_recipient", "person_id", "person", "SET NULL"),
]

# ── 6) Value-shape CHECKs: (name, table, expression). ──
_VALUE_SHAPE_CHECKS = [
    # L-1: the slug is the erasure confirmation token; pin the Pydantic pattern
    # (platform_schemas.org_slug) at the DB layer too.
    ("ck_organizations_slug_format", "organizations", "slug ~ '^[a-z0-9][a-z0-9-]*$'"),
    # L-5: provenance enums — extend the IN list with each new connector type.
    ("ck_person_email_source", "person_email", "source IS NULL OR source IN ('imap')"),
    ("ck_person_alias_source", "person_alias", "source IS NULL OR source IN ('imap')"),
    ("ck_company_domain_source", "company_domain", "source IS NULL OR source IN ('imap')"),
    # Hash shape verified against the producer: security/tokens.py sha256_hex() — lowercase
    # sha256 hexdigest, exactly 64 hex chars (table empty at migration time).
    ("ck_refresh_tokens_hash_shape", "refresh_tokens", "token_hash ~ '^[0-9a-f]{64}$'"),
    # L-8: absent filenames are NULL, never '' (the parser coalesces '' -> None).
    (
        "ck_email_attachment_filename_nonempty",
        "email_attachment",
        "filename IS NULL OR filename <> ''",
    ),
    # M-2: pin each status to its real write-path shape (see module docstring for the
    # deliberate divergence from the audit sketch on revoked-from-requested rows).
    (
        "ck_support_grant_lifecycle",
        "support_grant",
        "(status <> 'requested' OR (decided_at IS NULL AND decided_by_user_id IS NULL"
        " AND expires_at IS NULL))"
        " AND (status <> 'approved' OR (decided_at IS NOT NULL"
        " AND decided_by_user_id IS NOT NULL AND expires_at IS NOT NULL))"
        " AND (status <> 'denied' OR (decided_at IS NOT NULL"
        " AND decided_by_user_id IS NOT NULL AND expires_at IS NULL))"
        " AND (status <> 'revoked' OR (((decided_at IS NULL) = (decided_by_user_id IS NULL))"
        " AND (expires_at IS NULL OR decided_at IS NOT NULL)))",
    ),
]


def _composite_fk_name(fk: _CompositeFk) -> str:
    """Deterministic name for the new composite FK (all ≤ 63-char PG identifier limit)."""
    return f"fk_{fk.child}_{fk.column}_org"


def _original_fk_name(fk: _CompositeFk) -> str:
    """The superseded FK's PostgreSQL auto-generated name (verified on the live catalog)."""
    return f"{fk.child}_{fk.column}_fkey"


def _composite_ondelete_clause(fk: _CompositeFk) -> str:
    """ON DELETE phrase for the composite FK — SET NULL gets a PG-15+ column list so only the
    person pointer is nulled (a bare composite SET NULL would null org_id, which is NOT NULL)."""
    return fk.ondelete if fk.ondelete == "CASCADE" else f"SET NULL ({fk.column})"


def upgrade() -> None:
    # 1) Tenant-root FKs — no more phantom tenants (H-2). NO ACTION on delete: erasure hooks
    #    delete children before the org row; an accidental org DELETE must not cascade.
    for table in _ORG_FK_TABLES:
        op.create_foreign_key(f"fk_{table}_org_id", table, "organizations", ["org_id"], ["id"])

    # 2) Case-insensitive identity uniqueness (M-1) — DB-level backing for the Pydantic
    #    NormalizedEmail guarantee (lowercased login lookups must always match).
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_index(
        "uq_platform_admins_email_lower",
        "platform_admins",
        [sa.text("lower(email)")],
        unique=True,
    )

    # 3) Composite tenant-coherent FKs (M-3, L-10): parents anchor on UNIQUE (org_id, id), then
    #    each child FK is replaced by (org_id, parent_id) preserving its ON DELETE rule. Raw DDL
    #    for the FK adds: the SET NULL column-list syntax is PG-specific and version-proof here.
    for parent in _COMPOSITE_FK_PARENTS:
        op.create_unique_constraint(f"uq_{parent}_org_row", parent, ["org_id", "id"])
    for fk in _COMPOSITE_FKS:
        op.execute(
            f"ALTER TABLE {fk.child} ADD CONSTRAINT {_composite_fk_name(fk)} "
            f"FOREIGN KEY (org_id, {fk.column}) REFERENCES {fk.parent} (org_id, id) "
            f"ON DELETE {_composite_ondelete_clause(fk)}"
        )
        op.drop_constraint(_original_fk_name(fk), fk.child, type_="foreignkey")

    # 4) Recipient-edge uniqueness (M-6) — requires the within-message dedup cleanup first.
    op.create_unique_constraint(
        "uq_email_recipient_edge", "email_recipient", ["email_id", "kind", "address"]
    )

    # 5) Sync-ledger self-defense (L-2, L-3, L-4) — the "audit never lies" tables. The seen-sum
    #    CHECK (seen = stored+skipped+failed) is deliberately OMITTED: abandoned rows keep their
    #    0-defaults while seen may be > 0 mid-crash (enforced in the runner's finalize tests).
    op.create_unique_constraint("uq_sync_run_fencing", "connector_sync_run", ["org_id", "run_id"])
    op.create_check_constraint(
        "ck_sync_run_time_order",
        "connector_sync_run",
        "finished_at IS NULL OR finished_at >= started_at",
    )
    op.create_check_constraint(
        "ck_sync_run_terminal_finished",
        "connector_sync_run",
        "(status = 'running') = (finished_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_sync_run_counts_nonneg",
        "connector_sync_run",
        "messages_seen >= 0 AND messages_stored >= 0 AND "
        "messages_skipped >= 0 AND messages_failed >= 0",
    )
    op.create_check_constraint(
        "ck_sync_cursor_uidvalidity_positive",
        "connector_sync_cursor",
        "uidvalidity IS NULL OR uidvalidity > 0",
    )

    # 6) Value-shape CHECKs (L-1, L-5, L-8, M-2 + refresh-token hygiene).
    for name, table, expression in _VALUE_SHAPE_CHECKS:
        op.create_check_constraint(name, table, expression)

    # 7) DEFERRED on purpose (do with CA-CONN-04, not now): index on
    #    email_attachment(content_hash) for hash-keyed extraction caching / blob-store dedup.


def downgrade() -> None:
    # Exact 0013 restoration, in reverse order of upgrade().
    for name, table, _expression in reversed(_VALUE_SHAPE_CHECKS):
        op.drop_constraint(name, table, type_="check")

    op.drop_constraint(
        "ck_sync_cursor_uidvalidity_positive", "connector_sync_cursor", type_="check"
    )
    op.drop_constraint("ck_sync_run_counts_nonneg", "connector_sync_run", type_="check")
    op.drop_constraint("ck_sync_run_terminal_finished", "connector_sync_run", type_="check")
    op.drop_constraint("ck_sync_run_time_order", "connector_sync_run", type_="check")
    op.drop_constraint("uq_sync_run_fencing", "connector_sync_run", type_="unique")

    op.drop_constraint("uq_email_recipient_edge", "email_recipient", type_="unique")

    # Re-create each original single-column FK (original auto-generated name + original ON DELETE
    # rule), then drop its composite replacement and the parent anchors.
    for fk in reversed(_COMPOSITE_FKS):
        op.drop_constraint(_composite_fk_name(fk), fk.child, type_="foreignkey")
        op.create_foreign_key(
            _original_fk_name(fk),
            fk.child,
            fk.parent,
            [fk.column],
            ["id"],
            ondelete=fk.ondelete,
        )
    for parent in reversed(_COMPOSITE_FK_PARENTS):
        op.drop_constraint(f"uq_{parent}_org_row", parent, type_="unique")

    op.drop_index("uq_platform_admins_email_lower", table_name="platform_admins")
    op.drop_index("uq_users_email_lower", table_name="users")

    for table in reversed(_ORG_FK_TABLES):
        op.drop_constraint(f"fk_{table}_org_id", table, type_="foreignkey")
