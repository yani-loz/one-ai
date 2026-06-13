"""attachment extracted_data — persist the typed structured grid (xlsx slice, design §2.5)

Role: Lands the schema half of the xlsx extraction slice (design §2.5): email_attachment.
      extracted_data — a nullable JSONB column persisting ExtractionResult.structured, the typed,
      analysis-ready cell grid the xlsx extractor produces ('xlsx-grid-v1'). xlsx is DATA not prose,
      so it stores BOTH the bounded text render (extracted_text, embeddable) AND this faithful typed
      grid (lossless, for analysis-at-query-time later, e.g. via DuckDB). Every non-xlsx extractor
      leaves structured None, so the column is NULL for them — backward-compatible.
Used by: alembic upgrade head (runs as the OWNER role `oneai`, which owns every table). Both write
         seams (email_ingest_service + scripts.backfill_attachment_extraction) persist
         ExtractionResult.structured into this column.
Depends on: 0016_extraction_detail (revision chain); the email_attachment table created by
            0008_email_ingestion_schema.
Key invariants:
  - extracted_data is NULLABLE JSONB: NULL for every text/document format (structured is None) and
    for not-yet-extracted rows; populated only by formats that are DATA (xlsx/xlsm today).
  - No CHECK, no default, no index: the grid is analysis payload, not a query key — the structured
    schema lives in the extractor ('xlsx-grid-v1') and the backfill targets rows via
    extraction_status + extractor_name/extractor_version from 0015, never via this column.
  - No new GRANTs needed: the column rides email_attachment's existing table-level tenant grants
    (0009/0013) — a NEW COLUMN inherits the table's grants; no new table is created — and the
    table's existing org_id/RLS/connector-delete erasure lifecycle covers it unchanged.
  - No data migration: existing rows keep NULL (they were extracted before this slice; the backfill
    re-populates xlsx rows on its next run while the disk corpus exists).
  - downgrade() drops cleanly: just the one column (persisted grids are lost — the backfill can
    recompute them after a re-upgrade).

Revision ID: 0017_attachment_extracted_data
Revises: 0016_extraction_detail
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_attachment_extracted_data"
down_revision: str | None = "0016_extraction_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no default: adding it is metadata-only on PG 11+ (no table rewrite); rows the
    # xlsx extractor has not touched simply stay NULL (every non-xlsx format leaves it NULL).
    op.add_column(
        "email_attachment",
        sa.Column("extracted_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # Exact 0016 restoration: drop the one column added above.
    op.drop_column("email_attachment", "extracted_data")
