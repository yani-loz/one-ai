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
| D7 | **Every retrieval read runs on the person-bound reader plane** — `reader_session(org_id, person_id)` on the SELECT-only `oneai_reader` role, `person_id` taken ONLY from the verified auth binding (`principal_source_identity`). Tools carry ZERO tenant logic: org RLS + the PF-01 visibility policies enforce scope server-side, so a compromised prompt cannot widen it. | migration `0019_permission_fidelity` + `app/core/database.py`; commit `db1795d` | 2026-07-04 |
| D8 | **The reader answers under a citation contract** (`[id: uuid]` from a tool payload on every claim, mechanically gated) — it zeroed fabricated citations durably. And **deliver behaviour rules through the TOOL channel** (descriptions + payloads), not prompt-suffix procedures: tool-side changes fire consistently across reps, prompt procedures scatter (channel-consistency law). | EXP-002 §1 (MUT5) + `Benchmarks/_ask_loop/ledger.md`; commit `5463aef` | 2026-07-05 |
| D9 | **Grants derive ONLY from fields that are in the dedup key** — owner + `From` + `to`/`cc` (`DISCLOSED_RECIPIENT_KINDS`); bcc / reply-to / sender never mint a grant. Two copies of one message then derive identical grants *by construction*, so ingest ORDER can no longer decide who may read it. A third-party recipient display name (chosen by the sender about someone else) is dropped; naming yourself in `From:` stays allowed. | `imap/parsing/email_parser.py` + `access/services/grant_writer.py`; EXP-002 §7.29 — **working tree, uncommitted as of 2026-09-06** | 2026-07-26 |
| D10 | **Every gold set an optimization loop runs against is split three ways — optimization / test / validation — at leakage-group level, and the blinding is mechanical, not policy** (hidden root outside the working folder, deny rules, aggregates-only stdout, a persistent evaluation counter). The validation split is touched exactly once, by the founder, at final acceptance. The same data may never both tune and accept. | founder requirement; `../PM/memory/MEM-01/MEM-01-gold-standard.md` §1.3 + §10a | 2026-08-29 |

---

## ❓ Open Questions / Backlog

*Things worth testing, not yet started. Each line: the question + why it matters (what build decision depends on it). If nothing depends on the answer, don't test it.*

*Reviewed 2026-09-06 — the template example was removed. Questions recorded open elsewhere and not
yet closed:*

- [ ] **Small model + person-centric DB enrichment, or a bigger reader arm?** → decides where Ask spend goes. Recorded as the open strategic fork (founder decision pending) in [`EXP-002 §6`](EXP-002_ask-tools-loop-diary.md); the ledger has its decision instrument — the reader-oracle probe — only as *queued*, and no probe run exists under `Benchmarks/_ask_loop/runs/` (checked 2026-09-06).
- [ ] **Is EU-resident processing available for Cowork MCP tool traffic (arguments *and* results), and which data terms govern Cowork-via-Max?** → decides whether the MCP-01 rung-1 delivery is defensible for DACH customers. Recorded as the axis-2 open blocker (no Anthropic-primary source captured) in [`../PM/mcp/MCP-01-cowork-mcp-host-capability-report.md`](../PM/mcp/MCP-01-cowork-mcp-host-capability-report.md).

---

## 🧪 Experiment Log

*Newest first. One row per experiment. Keep the hypothesis and outcome to one line each — detail lives in the linked file.*

| ID | Date | Title | Hypothesis (one line) | Status | Outcome (one line) |
|----|------|-------|-----------------------|--------|--------------------|
| EXP-003 *(no in-repo experiment file — the plan lives in [`../PM/memory/MEM-01/MEM-01-experiments-and-goals.md`](../PM/memory/MEM-01/MEM-01-experiments-and-goals.md))* | 2026-07-26 (designed) | MEM-01 "ants over Ethera": measure a small extract-only colony over the email corpus, phase by phase | Measurement, not assertion — the plan states phase 1 ends in a REPORT, not a threshold: precision/recall per ant kind vs an Opus baseline, cost per 1,000 items, self-consistency, arm A (scout → specialists) vs arm B (combined read), across model arms | DESIGNED (not started) | Design complete and frozen-by-review: gold standard v5.3 (2026-09-06) + phased experiments plan + value catalog + `anchors_v0.yaml` under `docs/PM/memory/MEM-01/`. **No runner code exists** — the specified `backend/tools/mem01_verify/` and `backend/scripts/mem01/` are absent from the tree (checked 2026-09-06). EXP-003 gates MEM-01 implementation |
| [EXP-002](EXP-002_ask-tools-loop-diary.md) | 2026-07-04 | ASK-tools optimization loop: mutate the Ask tool layer until a ≤9B–31B reader maxes out the founder-cleaned gold set | One bounded mutation per cycle over tools/payloads/prompt raises a small reader's N=3 majority score; accepts only at full-dev with ≥ +3 questions over parent | 🟡 | Score arc 12.1% → 24.2% (qwen + MUT5) → 27.3% (Gemma trunk) → 33.3% v1 / **32.1% honest claim-level on v2**; zero fabricated citations in ~1,500+ graded episodes. Accepted: MUT5 (`5463aef`), CKPT2 trunk-v2 (`1feb172`); MUT11b accepted then rescinded (`d564df0` → `e4a4535`). Ledger state: **V2 plateau 8/20**, V2-COMPOSITE escalated at R1, R2 never journalled; newest scored run 2026-07-09. Last dated diary entry 2026-07-26 (§7.33 — red-team/hardening rounds, not scoring) |
| [EXP-001](EXP-001_small-llm-schema-optimization-loop.md) | 2026-07-04 | Schema-optimization loop: evolve the derived layer on a DB copy until a local 7–8B maxes out the gold eval | Deterministic spine + typed tools recover most of the v2 0–7% gap; the loop finds non-obvious interface wins; the plateau lands above the usefulness bar | ✅ | **Concluded 2026-07-07.** Built and run in the `EXP-001-schema-loop` lab world 2026-07-04 → 07-07 (Gemma-4-31B, qwen 9B, XiYanSQL lane, strong-solo and supervisor arms), judge-adjudicated with SQL evidence and verifier-audited. Ledger of record: `Experiments/Loops/EXP-001-schema-loop/archive/CAMPAIGN-1.md` (outside the repo) — closing line: **85% NOT reached**; experiments stopped per founder instruction, awaiting word to continue. Narrative: [EXP-002](EXP-002_ask-tools-loop-diary.md) §7 (final scoreboard §7.10); honest-number re-adjudication: [`../PM/ask/ASK-02-overnight-analysis-2026-07-07.md`](../PM/ask/ASK-02-overnight-analysis-2026-07-07.md) §1 |

---

## Conventions

- **File naming:** `EXP-XXX_short-slug.md` — zero-padded 3-digit ID, kebab-case slug. IDs are sequential and never reused (even if an experiment is abandoned).
- **Status legend:** 🟡 running · ✅ concluded · ❌ inconclusive / dead end · ⏸ paused
- **Tags** (free-form, reuse where possible): `retrieval`, `models`, `cost`, `connectors`, `memory`, `agent`, `frontend`, `infra`, `eval`.
- **Minimum viable entry:** even a 5-minute test gets a file. The mandatory sections are **Question → Results → Decision**; everything else in the template is optional for small experiments.

## EXP-002 — ASK-tools optimization loop (2026-07-04 → ongoing)

Full diary: [`EXP-002_ask-tools-loop-diary.md`](EXP-002_ask-tools-loop-diary.md) — every mutation,
result, and DO-NOT-REPEAT lesson of the Ask-layer tool campaign (qwen 12%→24% → Gemma 27-33% v1 →
v2 bench 32% honest). Headline settled findings: (1) **channel-consistency law** — tool-description/
payload instructions fire deterministically across reps, prompt-suffix procedures scatter;
(2) citation contract (`[id: uuid]` required) zeroes fabrication durably; (3) router/specialist and
text-to-SQL arms LOSE under a ≤9B delegator; (4) N=3 majority is the measurement floor on Together
at temp 0; (5) benchmark quality dominates — founder-cleaned fully-answerable set changed every
number. Authoritative journal: `Benchmarks/_ask_loop/ledger.md`.
