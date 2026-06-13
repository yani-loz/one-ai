"""attachment extraction status — CHECK-pinned vocabulary + extractor provenance (CA-CONN-04)

Role: Lands the schema half of the attachment-extraction design (docs/connect-attachment-
      extraction-design.md §2, Phase A): (1) email_attachment.extraction_status — a NOT NULL,
      CHECK-pinned closed vocabulary turning honest-NULL extracted_text into honest-NULL-WITH-A-
      REASON; (2) extractor_name/extractor_version — provenance for version-aware backfills
      (re-run exactly the rows an upgraded extractor would improve); (3) the (org_id,
      content_hash) lookup index migration 0014 explicitly deferred to CA-CONN-04 (hash-keyed
      extraction backfill/dedup); (4) a data migration stamping rows extracted by the v1
      text-only seam as 'extracted' (extractor_name 'legacy-text') so the backfill's 'pending'
      work-queue is exact.
Used by: alembic upgrade head (runs as the OWNER role `oneai`, which owns every table).
Depends on: 0014_data_quality_guards (revision chain); the email_attachment table created by
            0008_email_ingestion_schema.
Key invariants:
  - The CHECK's status set is the SAME literal vocabulary as
    app.connectors.extraction.extraction_result.EXTRACTION_STATUSES (the Python source of
    truth) — deliberately HARDCODED here, never imported: a migration is a frozen snapshot, and
    a future constant rename must not silently rewrite history.
    tests/db/test_attachment_extraction_status.py asserts the live CHECK and the Python set
    never drift. ('extracted_partial_scanned' joined the vocabulary during the 2026-06-11
    review cycle, while this migration was still uncommitted — editing it was sanctioned.)
  - Data migration: rows the v1 seam already extracted (extracted_text IS NOT NULL AND <> '')
    become 'extracted' with extractor_name 'legacy-text' (extractor_version NULL — v1 had no
    version concept); every other row keeps the 'pending' default and is the backfill's queue.
    email_attachment is a FORCE-RLS table, so the UPDATE runs inside a NO FORCE / FORCE bracket:
    FORCE applies row policies even to the table OWNER, and only superuser/BYPASSRLS roles are
    exempt — on a managed Postgres where the migration role is the plain owner the bare UPDATE
    would silently match zero rows (portability, not a local-stack need).
  - The new columns are nullable provenance except extraction_status (NOT NULL DEFAULT
    'pending'): adding a defaulted column is metadata-only on PG 11+ (no table rewrite).
  - No new GRANTs needed: columns inherit email_attachment's existing table-level tenant grants
    (0009/0013), and no new table is created.
  - downgrade() drops cleanly: index, CHECK, then the three columns (provenance is lost — the
    backfill script can recompute it after a re-upgrade).

Revision ID: 0015_attachment_extraction (the natural `0015_attachment_extraction_status` id is
             33 chars — one over alembic_version.version_num's varchar(32); the FILE keeps the
             full name)
Revises: 0014_data_quality_guards
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_attachment_extraction"
down_revision: str | None = "0014_data_quality_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# KEEP IN SYNC with app.connectors.extraction.extraction_result.EXTRACTION_STATUSES (sorted;
# hardcoded by migration convention — see module docstring). The standing test enforces parity.
_EXTRACTION_STATUSES = (
    "corrupt",
    "empty",
    "encrypted",
    "extracted",
    "extracted_partial_scanned",
    "pending",
    "scanned_pending_ocr",
    "skipped_nondocument",
    "skipped_oversize",
    "truncated",
    "unsupported_format",
)

_STATUS_CHECK = "extraction_status IN ({})".format(
    ", ".join(f"'{status}'" for status in _EXTRACTION_STATUSES)
)


def upgrade() -> None:
    # 1) The status + provenance columns. NOT NULL DEFAULT 'pending' backfills every existing
    #    row as not-yet-attempted; the provenance pair stays NULL until an extractor runs.
    op.add_column(
        "email_attachment",
        sa.Column("extraction_status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.add_column("email_attachment", sa.Column("extractor_name", sa.Text(), nullable=True))
    op.add_column("email_attachment", sa.Column("extractor_version", sa.Text(), nullable=True))

    # 2) Pin the closed vocabulary at the DB layer (the Python set is the source of truth).
    op.create_check_constraint(
        "ck_email_attachment_extraction_status", "email_attachment", _STATUS_CHECK
    )

    # 3) Data migration: rows the v1 text-only seam already extracted are DONE, not pending —
    #    stamped 'legacy-text' so a version-aware backfill can tell them from the new engines.
    #    ('' should not exist — the v1 seam coalesced blank → NULL — but is guarded anyway.)
    #    email_attachment is FORCE-RLS (0009): FORCE applies the tenant policies even to the
    #    table OWNER, and without the org GUC set the UPDATE would silently match ZERO rows
    #    unless the migration role happens to be superuser/BYPASSRLS. Bracketing with
    #    NO FORCE → UPDATE → FORCE keeps the step portable to owner-without-BYPASSRLS setups
    #    (e.g. managed Postgres) and restores the standing RLS posture immediately after.
    op.execute("ALTER TABLE email_attachment NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE email_attachment SET extraction_status = 'extracted', "
        "extractor_name = 'legacy-text' "
        "WHERE extracted_text IS NOT NULL AND extracted_text <> ''"
    )
    op.execute("ALTER TABLE email_attachment FORCE ROW LEVEL SECURITY")

    # 4) The hash-keyed extraction lookup 0014 deferred to CA-CONN-04: the backfill matches
    #    disk attachments to rows by (org_id, content_hash); future blob-store dedup reuses it.
    op.create_index(
        "ix_email_attachment_org_content_hash", "email_attachment", ["org_id", "content_hash"]
    )


def downgrade() -> None:
    # Exact 0014 restoration, in reverse order of upgrade().
    op.drop_index("ix_email_attachment_org_content_hash", table_name="email_attachment")
    op.drop_constraint("ck_email_attachment_extraction_status", "email_attachment", type_="check")
    op.drop_column("email_attachment", "extractor_version")
    op.drop_column("email_attachment", "extractor_name")
    op.drop_column("email_attachment", "extraction_status")
