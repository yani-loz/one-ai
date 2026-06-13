"""
Role: The pieces every attachment extractor shares — the stored-text size cap, the ONE table
      serializer, and installed-package version lookup for extraction provenance. Split out of
      pdf.py (A2: the per-format modules grew past the ceiling carrying shared helpers, and
      docx/tnef importing them FROM pdf made the PDF module a false dependency hub).
Used by: extractors.pdf / .docx / .tnef (and every future per-format extractor);
         tests assert the serializer's shape directly.
Depends on: stdlib only (functools.cache, importlib.metadata).
Key invariants:
  - MAX_EXTRACTED_CHARS is THE storage cap for every extractor: text over it is truncated with
    status `truncated` and the capped text STORED (design §2.1 size discipline). One constant,
    one meaning — a per-format cap would make coverage numbers incomparable.
  - serialize_table is the ONE table-to-text shape (pipe-joined rows): PDF tables, docx tables,
    and any future format must render identically so chunking/retrieval see one convention.
  - package_version is cached and never raises ('unknown' for vendored installs) — it feeds the
    extractor_version provenance column that version-aware backfills target.
"""

from __future__ import annotations

from functools import cache
from importlib import metadata

# Extraction cap (chars): a pathological text layer must not balloon a row / the embedding
# pipeline. Every extractor stores AT MOST this many chars (status `truncated` beyond it).
MAX_EXTRACTED_CHARS = 2_000_000


def serialize_table(table: list[list[str | None]]) -> str:
    """Serialize one extracted table as pipe-joined rows (design §2.1).

    One line per row, cells joined with ' | '; None cells become '', cell-internal newlines
    flatten to spaces. Returns '' for a table with no cell text at all (nothing worth appending).
    """
    lines = [" | ".join((cell or "").replace("\n", " ").strip() for cell in row) for row in table]
    if not any(line.strip(" |") for line in lines):
        return ""
    return "\n".join(lines)


@cache
def package_version(package: str) -> str:
    """The installed package version (provenance for version-aware backfills); 'unknown' if the
    distribution metadata is unavailable (e.g. a vendored install)."""
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"
