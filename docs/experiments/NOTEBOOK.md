# Experiments Notebook — One AI MVP

> **What this is.** The running lab notebook for the build. Every experiment gets its own
> `EXP-XXX_slug.md` file (use `_TEMPLATE.md`). This notebook is the **index + running log +
> distilled conclusions** — the one place to scan everything that's been tried and what it proved.
>
> **Workflow:**
> 1. Have a question → add it to **Open Questions** (or just start an experiment).
> 2. Starting an experiment → create `EXP-XXX_slug.md` from the template, add a row to **Experiment Log** with status 🟡.
> 3. Finishing → update the row's status + one-line outcome.
> 4. When an experiment **settles** a question → copy its one-line decision into **Settled Decisions**. That table is what feeds the build spec.
>
> **Golden rule:** capture the *reasoning* and the *decision*, not just the result. The result you'll remember for a week; *why* you decided evaporates in days.

---

## ✅ Settled Decisions

*Conclusions that have graduated out of experiments. These are directives for the build — pull from here into the spec/code. State each as an instruction, not a finding.*

| # | Decision (as a directive) | Source | Date |
|---|---------------------------|--------|------|
| D1 | Dedup email by **content identity**, never raw bytes: hash decoded Message-ID + From/Subject + UTC send instant + canonical To/Cc + body_text + text/html digest + sorted attachment identities (TNEF contributes its stable embedded-interior digest); no-Message-ID & headers-only fall back to `sha256(raw_bytes)`. Outlook regenerates serialization per folder copy, so any raw-byte hash fragments (was 39% duplicate rows). Key recipe = v5; changing it requires a corpus re-ingest. | `email_parser`/`dedup_key`; audit `2026-06-10_db-data-quality` H-1 | 2026-06-13 |
| D2 | The file/attachment **text-extraction pipeline is connector-agnostic** — lives in `app/connectors/extraction/` (`extract_*(bytes) -> ExtractionResult`, never raises, honest status), imports nothing from any connector. Each connector supplies its own storage. CON-04 (local folders) reuses it directly. | relocation `39b566d` | 2026-06-13 |
| D3 | **Spreadsheets are DATA, not prose** — store DUAL: a bounded text render (embed/find) AND a faithful typed cell grid (`xlsx-grid-v1`, lossless) in `email_attachment.extracted_data` (JSONB, `none_as_null=True`). Analysis is deferred to **query time via DuckDB** (the agent writes SQL over the grid). "Dumb lossless ingest, smart query later." First instance of the Bible §6 Structured + Explorable memory types. | `extraction/xlsx`; design §2.5 | 2026-06-13 |
| D4 | Extraction libraries (all MIT/BSD/LGPL-import — **no AGPL**): PDF → pdfplumber + pypdf; docx → python-docx; xlsx → openpyxl; TNEF → tnefparse + compressed-rtf + striprtf. Vendor loggers that interpolate payload bytes (pypdf, tnefparse) are muted at import. OOXML formats (docx/xlsx) share one zip-bomb guard (`extraction/ooxml`). | extraction slices | 2026-06-13 |
| D5 | **OCR is Phase C, deferred and status-marked in the data** (`scanned_pending_ocr` / `extracted_partial_scanned`): local Tesseract `bul+deu+eng`, confidence-gated → honest NULL + HiTL below threshold. Cloud/LLM OCR only under zero-retention + EU-DPA + per-tenant opt-in. Image OCR not built (0 doc-scans measured in the corpus). | design §3; FIX_BEFORE_PROD CA-CONN-04 | 2026-06-13 |
| D6 | The **sync layer is IMAP-specialized today and that is documented, not hidden** (`IncrementalFetch` carries folder/UIDVALIDITY cursors). The connector-blind core is the run-ledger, the registry, and the connection/credential plane. Generalize the fetch/cursor abstraction at the **second** fetching connector (n=2), never from one example. | relocation `39b566d` | 2026-06-13 |

---

## ❓ Open Questions / Backlog

*Things worth testing, not yet started. Each line: the question + why it matters (what build decision depends on it). If nothing depends on the answer, don't test it.*

- [ ] *(example — delete) Which embedding model gives best recall/€ on our doc mix? → picks the production embedder.*

---

## 🧪 Experiment Log

*Newest first. One row per experiment. Keep the hypothesis and outcome to one line each — detail lives in the linked file.*

| ID | Date | Title | Hypothesis (one line) | Status | Outcome (one line) |
|----|------|-------|-----------------------|--------|--------------------|
| [EXP-001](EXP-001_small-llm-schema-optimization-loop.md) | 2026-07-04 | Schema-optimization loop: evolve the derived layer on a DB copy until a local 7–8B maxes out the gold eval | Deterministic spine + typed tools recover most of the v2 0–7% gap; the loop finds non-obvious interface wins; the plateau lands above the usefulness bar | ⏸ | Designed + Codex gap-reviewed same night (16 findings adopted, §4.12): first run is a PILOT; sealed 3-way split, typed graders, PII-free git, disposable DBs, state-machine runner. Awaiting sign-off: target %, budget-from-pilot, model pick, cloud arm (cut for pilot) |

---

## Conventions

- **File naming:** `EXP-XXX_short-slug.md` — zero-padded 3-digit ID, kebab-case slug. IDs are sequential and never reused (even if an experiment is abandoned).
- **Status legend:** 🟡 running · ✅ concluded · ❌ inconclusive / dead end · ⏸ paused
- **Tags** (free-form, reuse where possible): `retrieval`, `models`, `cost`, `connectors`, `memory`, `agent`, `frontend`, `infra`, `eval`.
- **Minimum viable entry:** even a 5-minute test gets a file. The mandatory sections are **Question → Results → Decision**; everything else in the template is optional for small experiments.
