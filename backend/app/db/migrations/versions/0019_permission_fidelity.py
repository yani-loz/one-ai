"""permission fidelity (PF-01) — ACL-at-ingest + within-tenant visibility RLS

Org-level RLS (0009) isolates TENANTS; this migration closes the WITHIN-tenant hole before any
retrieval table exists (EPIC-PF-01's gate): today every employee's AI could read every other
employee's mailbox the moment a chunk table lands. Mirrors the app.access.models:

  - acl_grant                 — one principal's right to retrieve one content object; revocation
                                is a tombstone (revoked_at) that stops matching immediately.
  - visibility_promotion      — the ONLY audience-widening path: append-only (UPDATE-rejected),
                                human approver + audit anchor NOT NULL (AC5).
  - principal_source_identity — verified external-identity → person mapping; grants resolve ONLY
                                through verified rows (AC8, UNKNOWN ⇒ DENY).
  - fact_provenance           — fail-closed anchor schema for future Learn facts (AC6).

THE READER PLANE (the load-bearing design decision, discovered live): PostgreSQL applies
SELECT policies to the rows RETURNED by INSERT ... RETURNING — so a restrictive SELECT
visibility policy on the WRITE role would make person-less ingest unable to insert the very
restricted rows it creates (every ORM insert RETURNINGs server defaults). One role cannot be
both the person-less system/write plane and the person-scoped fail-closed read plane. This
migration therefore creates the fourth role the epic back-pocketed (§2, B's graft):

    oneai_reader — NOLOGIN-until-provisioned, NOSUPERUSER, NO BYPASSRLS, SELECT-only on the
    tenant surface (plus INSERT on audit_log for decision telemetry). The person-scoped
    retrieval plane: agents/tools/retrieval run HERE and physically cannot write or widen.

Content tables (email_message / email_recipient / email_attachment) gain
visibility_scope ('restricted' default) + IMMUTABLE origin_scope + container_id, and a
RESTRICTIVE `visibility` policy FOR SELECT TO oneai_reader:

    visible ⇔ visibility_scope = 'org' OR a live acl_grant for app.current_person_id matches
    (children match on their PARENT's id — the AC22 chunk→grant contract, exercised today).

The person GUC is read with NULLIF(current_setting(...), '') — unset ⇒ NULL ⇒ only org-scope
rows (AC3, fail-closed ON THE READER PLANE). oneai_app (ingest/admin/system writes) keeps
org-RLS-only visibility — trusted first-party service code, exactly as before this migration;
within-tenant rules on that plane are enforced in services (ownership checks), as the CO-01
admin plane already does.

Lifecycle note (AC4): pause (= disabled_at) deliberately keeps rows visible; disconnect is a
row DELETE today (children cascade away), so no status predicate is needed in the policy yet —
if a retained 'disconnected' state is ever introduced, its predicate MUST be added here.

Triggers:
  - <table>_origin_immutable    — origin_scope is set once at ingest, never updated.
  - <table>_lineage_guard       — INSERT and UPDATE: a restricted-origin row reaching
                                  visibility_scope='org' without promotion lineage is rejected.
  - visibility_promotion_no_update — append-only (DELETE stays allowed: the GDPR erasure hook;
                                  lineage survives in the retained audit_log, Art. 17(3)).

Per 0013's convention every new TenantMixin table is ENABLE + FORCE RLS with org_isolation AND
an explicit oneai_app DML grant.

Revision ID: 0019_permission_fidelity
Revises: 0018_connector_authorization
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_permission_fidelity"
down_revision: str | None = "0018_connector_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runtime roles (0009/0013 idiom) + the PF-01 retrieval role this migration creates.
_APP_ROLE = "oneai_app"
_READER_ROLE = "oneai_reader"
_ORG_GUC = "current_setting('app.current_org_id', true)::uuid"
# The person GUC: NULLIF guards the ''-set edge; unset ⇒ NULL ⇒ no grant matches (AC3).
_PERSON_GUC = "NULLIF(current_setting('app.current_person_id', true), '')::uuid"

_NEW_TENANT_TABLES = [
    "acl_grant",
    "visibility_promotion",
    "principal_source_identity",
    "fact_provenance",
]

# Content tables + the column each one's grants key on (AC22: children bind to the PARENT id).
_CONTENT_TABLES: list[tuple[str, str]] = [
    ("email_message", "id"),
    ("email_recipient", "email_id"),
    ("email_attachment", "email_id"),
]

# The retrieval plane's read surface: every tenant table a person-scoped query may touch —
# content + entity graph + the access tables (the policy subquery reads acl_grant AS the
# session role) + connection metadata (future lifecycle predicate). SELECT only; RLS applies.
_READER_SELECT_TABLES = [
    "email_message",
    "email_recipient",
    "email_attachment",
    "person",
    "person_email",
    "person_alias",
    "company",
    "company_domain",
    "person_company",
    "connector_connection",
    "acl_grant",
    "visibility_promotion",
    "principal_source_identity",
    "fact_provenance",
]


def _enforce_tenant_rls(table: str) -> None:
    """ENABLE + org_isolation policy + FORCE + explicit oneai_app DML grant (0013 convention)."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY org_isolation ON {table}
            USING (org_id = {_ORG_GUC})
            WITH CHECK (org_id = {_ORG_GUC})
        """
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {_APP_ROLE}")


def _timestamps() -> list[sa.Column]:
    """The TimestampMixin columns (server-maintained created_at / updated_at)."""
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ── 0) The retrieval role (0009 idiom: shape in the migration, LOGIN password out-of-band
    #       via scripts.provision_roles). SELECT-only by construction: it receives no INSERT/
    #       UPDATE/DELETE grant on any tenant table — the agent plane cannot write, ever.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_READER_ROLE}') THEN
                CREATE ROLE {_READER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_READER_ROLE}")
    for table in _READER_SELECT_TABLES:
        if table not in _NEW_TENANT_TABLES:  # new tables get their reader grant after CREATE
            op.execute(f"GRANT SELECT ON TABLE {table} TO {_READER_ROLE}")
    # Decision telemetry (AC16): retrieval sessions append allow/deny audit rows. INSERT alone
    # is NOT enough — the ORM flush emits INSERT ... RETURNING (server-default id/occurred_at)
    # and Postgres demands SELECT privilege on the returned columns (2026-07-04 review P1), so
    # the reader gets policy-scoped SELECT too (org_isolation applies: own-org rows only —
    # mirroring oneai_app's 0013 posture on this table). The 0005 trigger keeps it append-only.
    op.execute(f"GRANT INSERT, SELECT ON TABLE audit_log TO {_READER_ROLE}")

    # ── 1) Visibility columns on the content tables (fail-closed defaults; existing corpus rows
    #       become restricted-origin restricted-visibility — re-granted on the next ingest pass).
    for table, _ in _CONTENT_TABLES:
        op.add_column(
            table,
            sa.Column("visibility_scope", sa.TEXT(), nullable=False, server_default="restricted"),
        )
        op.add_column(
            table,
            sa.Column("origin_scope", sa.TEXT(), nullable=False, server_default="restricted"),
        )
        op.add_column(table, sa.Column("container_id", postgresql.UUID(as_uuid=True)))
        op.create_check_constraint(
            f"ck_{table}_visibility_scope", table, "visibility_scope IN ('org', 'restricted')"
        )
        # EMAIL IS NEVER BORN ORG-VISIBLE (2026-07-04 review M6): the lineage guard legitimately
        # passes born-org rows (public-Slack territory), so a writer LYING about origin could
        # widen without lineage. Email content is recipient-granted by design — pin its origin
        # to 'restricted' at the DB, closing the org-born side door for these tables entirely.
        op.create_check_constraint(
            f"ck_{table}_origin_scope", table, "origin_scope = 'restricted'"
        )
    # Backfill container_id (email's container = the connection/mailbox).
    op.execute("UPDATE email_message SET container_id = connection_id")
    for child in ("email_recipient", "email_attachment"):
        op.execute(
            f"""
            UPDATE {child} AS c
            SET container_id = m.connection_id
            FROM email_message AS m
            WHERE m.id = c.email_id AND m.org_id = c.org_id
            """
        )

    # ── 2) acl_grant ─────────────────────────────────────────────────────────────────────────
    op.create_table(
        "acl_grant",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.TEXT(), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provenance", sa.TEXT(), nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "object_type IN ('email_message', 'mailbox')", name="ck_acl_grant_object_type"
        ),
        sa.CheckConstraint(
            "provenance IN ('owner', 'recipient', 'sender')", name="ck_acl_grant_provenance"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_acl_grant_org_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="CASCADE",
            name="fk_acl_grant_person_id_org",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connector_connection.org_id", "connector_connection.id"],
            ondelete="CASCADE",
            name="fk_acl_grant_connection_id_org",
        ),
    )
    op.create_index("ix_acl_grant_org_id", "acl_grant", ["org_id"])
    op.create_index("ix_acl_grant_person_id", "acl_grant", ["person_id"])
    op.create_index(
        "uq_acl_grant_live",
        "acl_grant",
        ["org_id", "object_type", "object_id", "person_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_acl_grant_person_object_live",
        "acl_grant",
        ["org_id", "person_id", "object_type", "object_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ── 3) visibility_promotion ──────────────────────────────────────────────────────────────
    op.create_table(
        "visibility_promotion",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.TEXT(), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_scope", sa.TEXT(), server_default="restricted", nullable=False),
        sa.Column("to_scope", sa.TEXT(), server_default="org", nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "from_scope = 'restricted' AND to_scope = 'org'",
            name="ck_visibility_promotion_direction",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_visibility_promotion_org_id"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "approved_by_user_id"],
            ["users.org_id", "users.id"],
            name="fk_visibility_promotion_approver",
        ),
        sa.ForeignKeyConstraint(
            ["audit_log_id"], ["audit_log.id"], name="fk_visibility_promotion_audit"
        ),
    )
    op.create_index("ix_visibility_promotion_org_id", "visibility_promotion", ["org_id"])
    op.create_index(
        "ix_visibility_promotion_object",
        "visibility_promotion",
        ["org_id", "object_type", "object_id"],
    )

    # ── 4) principal_source_identity ─────────────────────────────────────────────────────────
    op.create_table(
        "principal_source_identity",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.TEXT(), nullable=False),
        sa.Column("external_id", sa.TEXT(), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verified_by_user_id", postgresql.UUID(as_uuid=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_type IN ('auth', 'email')", name="ck_principal_source_identity_type"
        ),
        sa.UniqueConstraint(
            "org_id", "source_type", "external_id", name="uq_principal_source_identity"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_principal_source_identity_org_id"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="CASCADE",
            name="fk_principal_source_identity_person",
        ),
    )
    op.create_index("ix_principal_source_identity_org_id", "principal_source_identity", ["org_id"])
    op.create_index(
        "ix_principal_source_identity_person_id", "principal_source_identity", ["person_id"]
    )

    # ── 5) fact_provenance (schema now; Learn populates in Step 7) ───────────────────────────
    op.create_table(
        "fact_provenance",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_object_type", sa.TEXT(), nullable=False),
        sa.Column("source_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.TEXT(), server_default="active", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('active', 'quarantined')", name="ck_fact_provenance_status"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_fact_provenance_org_id"),
    )
    op.create_index("ix_fact_provenance_org_id", "fact_provenance", ["org_id"])
    op.create_index("ix_fact_provenance_fact", "fact_provenance", ["org_id", "fact_id"])
    op.create_index(
        "ix_fact_provenance_source",
        "fact_provenance",
        ["org_id", "source_object_type", "source_object_id"],
    )

    # ── 6) Tenant RLS + grants on the four new tables (+ the reader's SELECT) ────────────────
    for table in _NEW_TENANT_TABLES:
        _enforce_tenant_rls(table)
        op.execute(f"GRANT SELECT ON TABLE {table} TO {_READER_ROLE}")
    # Append-only lineage is a PRIVILEGE property too (2026-07-04 review P2): the no-UPDATE
    # trigger alone would let tenant-plane code DELETE an approval row while the content stays
    # org-visible. The tenant role loses UPDATE AND DELETE (trigger + privilege, belt and
    # braces); the GDPR erasure hook runs on the BYPASS global role (auto-granted since 0009)
    # and is unaffected.
    op.execute(f"REVOKE UPDATE, DELETE ON TABLE visibility_promotion FROM {_APP_ROLE}")

    # ── 7) RESTRICTIVE visibility policies on the content tables (ANDed with org_isolation).
    #       TO oneai_reader ONLY: the write plane (oneai_app) must keep reading restricted rows
    #       person-lessly (dedup SELECTs, extraction reuse, INSERT..RETURNING — PG applies
    #       SELECT policies to RETURNING rows, which is what forced the role split).
    for table, grant_key in _CONTENT_TABLES:
        op.execute(
            f"""
            CREATE POLICY visibility ON {table}
                AS RESTRICTIVE
                FOR SELECT
                TO {_READER_ROLE}
                USING (
                    visibility_scope = 'org'
                    OR EXISTS (
                        SELECT 1
                        FROM acl_grant g
                        WHERE g.org_id = {table}.org_id
                          AND g.person_id = {_PERSON_GUC}
                          AND g.revoked_at IS NULL
                          AND g.object_type = 'email_message'
                          AND g.object_id = {table}.{grant_key}
                    )
                )
            """
        )

    # ── 8) Triggers: origin immutability + the AC5 lineage guard (INSERT AND UPDATE) ─────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION access_origin_scope_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.origin_scope IS DISTINCT FROM OLD.origin_scope THEN
                RAISE EXCEPTION 'origin_scope is immutable — set once at ingest (PF-01 AC5): %.%',
                    TG_TABLE_NAME, OLD.id;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION access_visibility_lineage_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            lineage_object_type text := TG_ARGV[0];
            lineage_object_id uuid;
        BEGIN
            IF NEW.origin_scope = 'restricted' AND NEW.visibility_scope = 'org' THEN
                lineage_object_id := (to_jsonb(NEW) ->> TG_ARGV[1])::uuid;
                IF NOT EXISTS (
                    SELECT 1 FROM visibility_promotion vp
                    WHERE vp.org_id = NEW.org_id
                      AND vp.object_type = lineage_object_type
                      AND vp.object_id = lineage_object_id
                      AND vp.approved_by_user_id IS NOT NULL
                      AND vp.audit_log_id IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        'restricted-origin row cannot be org-visible without promotion lineage '
                        '(PF-01 AC5): % % %', TG_TABLE_NAME, lineage_object_type, lineage_object_id;
                END IF;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table, grant_key in _CONTENT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_origin_immutable
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION access_origin_scope_immutable()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_lineage_guard
                BEFORE INSERT OR UPDATE ON {table}
                FOR EACH ROW
                EXECUTE FUNCTION access_visibility_lineage_guard('email_message', '{grant_key}')
            """
        )

    # ── 9) visibility_promotion append-only (UPDATE rejected; DELETE = erasure hook only) ────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION access_promotion_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'visibility_promotion is append-only — widening history cannot be rewritten '
                '(PF-01 AC5/S3)';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_visibility_promotion_no_update
            BEFORE UPDATE ON visibility_promotion
            FOR EACH ROW EXECUTE FUNCTION access_promotion_append_only()
        """
    )


def downgrade() -> None:
    # Defensive leftover from an earlier 0019 draft that shipped a SECURITY DEFINER lookup
    # (superseded by the reader-role design) — IF EXISTS makes this a no-op everywhere else.
    op.execute("DROP FUNCTION IF EXISTS email_prior_extraction(uuid, text, text)")
    op.execute("DROP TRIGGER IF EXISTS trg_visibility_promotion_no_update ON visibility_promotion")
    op.execute("DROP FUNCTION IF EXISTS access_promotion_append_only()")
    for table, _ in _CONTENT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_lineage_guard ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_origin_immutable ON {table}")
        op.execute(f"DROP POLICY IF EXISTS visibility ON {table}")
    op.execute("DROP FUNCTION IF EXISTS access_visibility_lineage_guard()")
    op.execute("DROP FUNCTION IF EXISTS access_origin_scope_immutable()")
    for table in reversed(_NEW_TENANT_TABLES):
        op.drop_table(table)
    for table, _ in _CONTENT_TABLES:
        op.drop_constraint(f"ck_{table}_origin_scope", table, type_="check")
        op.drop_constraint(f"ck_{table}_visibility_scope", table, type_="check")
        op.drop_column(table, "container_id")
        op.drop_column(table, "origin_scope")
        op.drop_column(table, "visibility_scope")
    # The reader role's grants on surviving tables are revoked; the role itself is CLUSTER-level
    # and deliberately kept (0009 keeps its roles too — idempotent create on re-upgrade). Guarded
    # on role existence so downgrading a DB that predates the role is a clean no-op.
    surviving = [t for t in _READER_SELECT_TABLES if t not in _NEW_TENANT_TABLES]
    revokes = "".join(f"REVOKE SELECT ON TABLE {table} FROM {_READER_ROLE};" for table in surviving)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_READER_ROLE}') THEN
                {revokes}
                REVOKE INSERT ON TABLE audit_log FROM {_READER_ROLE};
            END IF;
        END
        $$
        """
    )
