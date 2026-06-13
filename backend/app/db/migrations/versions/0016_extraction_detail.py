"""extraction detail — persist ExtractionResult.detail (EQ-7, CA-CONN-04 Phase B)

Role: Lands the schema half of audit finding EQ-7 (docs/audits/2026-06-10_db-data-quality-audit.md
      §7.2): email_attachment.extraction_detail — a nullable text column persisting
      ExtractionResult.detail, which both write seams (email_ingest_service and
      scripts.backfill_attachment_extraction) previously DROPPED. Truncation reasons,
      image_only_pages=N OCR backlog counts and corrupt exception class names now survive past
      the disk corpus's lifetime.
Used by: alembic upgrade head (runs as the OWNER role `oneai`, which owns every table).
Depends on: 0015_attachment_extraction (revision chain); the email_attachment table created by
            0008_email_ingestion_schema.
Key invariants:
  - extraction_detail is NULLABLE free-form text BUT bound by the ExtractionResult contract:
    exception CLASS names / fixed phrases / counts only — never attachment content verbatim
    (the never-embed-content invariant extraction_result.py already pins; no DB CHECK can
    express it, so the contract + tests own it).
  - No CHECK, no default, no index: detail is diagnostic provenance, not a query key (the
    backfill targets rows via extraction_status + extractor_name/extractor_version from 0015).
  - No new GRANTs needed: the column inherits email_attachment's existing table-level tenant
    grants (0009/0013), and no new table is created.
  - No data migration: existing rows keep NULL — their details were dropped at write time and
    are recomputable only by the backfill while the disk corpus exists (the EQ-7 finding).
  - downgrade() drops cleanly: just the one column (persisted details are lost — the backfill
    script can recompute them after a re-upgrade).

Revision ID: 0016_extraction_detail
Revises: 0015_attachment_extraction
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_extraction_detail"
down_revision: str | None = "0015_attachment_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no default: adding it is metadata-only on PG 11+ (no table rewrite); rows the
    # extractors have not touched yet simply stay NULL.
    op.add_column("email_attachment", sa.Column("extraction_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    # Exact 0015 restoration: drop the one column added above.
    op.drop_column("email_attachment", "extraction_detail")
