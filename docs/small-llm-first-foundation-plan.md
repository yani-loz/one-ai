# The small-LLM-first foundation plan — DB + architecture before the Ask layer

> **Status: PROPOSED — awaiting sign-off.** Drafted 2026-07-04 from five reconciled sources:
> (1) this repo's own evidence (the v2 small-model benchmark, the 2026-07-03/04 IMAP data-quality
> audits, the measured ingest profile), (2) a Codex/GPT cross-vendor consultation that inspected
> the live code + DB, (3) the research digest `02_Research/Technology/Database Architecture for
> LLM Agent Consumption.md` (measured-benchmark-graded), (4) the PF-01/CO-01 epics already in
> `docs/PM/`, and (5) **`docs/connect-memory-db-optimization-study.md`** — the parallel 5-design
> adversarial-panel study (26 agents + its own Codex pass), reconciled in §9 below. The two
> documents are complementary: the STUDY is the panel record and carries the fine-grained
> Phase-0/1 mechanics (embedding defaults, erasure-recompute, reply-obligation, trust zones);
> THIS plan is the decision record and carries the sequencing gates (PF-01), the tool/semantic
> layer, tags/industry packs, and the performance lane. Where they conflict, §9 is binding.
> Nothing here is built yet except where marked DONE.

## 1. The thesis (agreed)

**Treat the database as the AI's primary instrument. The smaller the query-time model, the less
responsibility it may carry for joins, arithmetic, authorization, ambiguity resolution, and
empty-result interpretation — so that intelligence moves into the data at WRITE time
(deterministic code first, bounded LLM enrichment second) and into a typed tool layer at READ
time.** The model chooses intent and phrases answers; it never writes raw SQL against normalized
tables, never reconciles rows, never interprets silence.

Evidence anchors: v2 measured small cloud models at **0–7% strict pass** on query-time agentic
synthesis (tool SELECTION was fixable to 93%; synthesis was not) while Claude-class scored
77–95%. A 2026 semantic-layer benchmark found +17–23pp from explicit semantics — more than model
choice. Enterprise text-to-SQL over raw schemas collapses (BEAVER 10.8%, Spider 2.0 ≤21%).

## 2. The three-layer architecture

| Layer | Contents | Who touches it |
|---|---|---|
| **Raw (system of record)** | Today's normalized tables: email_message/recipient/attachment, person/company graph, connector plane. Deterministic parse only; no LLM ever writes here. | Ingest code. The agent NEVER queries it directly. |
| **Derived / refined** | Versioned projection TABLES (never matviews — no RLS): conversations (threads), relationship edges, entity dossiers, `content_item` + `chunk`, tags, enrichments (summaries, language, facts). Every row: org_id + RLS, visibility/ACL binding, provenance (source hash, algorithm/model+prompt version, timestamps), erasure-hooked. | Projection/enrichment workers (async, outbox-fed — never inline in the ingest hot path). |
| **Tool / semantic** | Typed query tools returning ANSWER PACKETS: `{status: ok\|no_match\|ambiguous\|not_authorized\|not_indexed\|partial, answer, coverage, evidence_ids}` + a guarded read-only SQL escape hatch (validate → execute → one repair pass). Compact M-Schema-style serialization of only the exposed relations; semantics-as-data (canonical business-term definitions). | The agent (any size). Small adaptive tool shortlist, concise standardized descriptions. |

Postgres-native retrieval before embeddings: stored `tsvector` (config `simple` — no built-in
Bulgarian stemmer; language column is NULL today) + `websearch_to_tsquery` + `ts_headline`
snippets as evidence; `pg_trgm` + `unaccent` on person/company aliases for fuzzy entity lookup.
Honest naming: `ts_rank` is lexical ranking, NOT BM25 — a real BM25 extension is P2,
benchmark-gated.

Schema hygiene rules that ride along: any attribute the agent filters on repeatedly gets PROMOTED
from JSONB to a typed (or generated) column — JSONB is for long-tail enrichment payloads only;
closed CHECK-pinned vocabularies for every state column (extraction_status is the template);
descriptive names + `COMMENT ON` as cheap maintainer/big-model documentation (the measured lever
is the description IN the tool schema, so comments are hygiene, not a pillar).

## 3. P0 — must land BEFORE chunks/embeddings exist

1. **PF-01 permission-faithful retrieval** (`docs/PM/permission-fidelity/EPIC-PF-01`, spec'd
   2026-06-09, not built). Org RLS says nothing about WITHIN-tenant privacy: the moment a chunk
   table exists, every employee's AI could read every other employee's private mailbox. The epic
   itself states the gate: *"chunk and every retrieval table is built only AFTER this lands."*
   ACL-at-ingest, principal GUC, visibility RLS on every content/projection table. **The single
   biggest omission caught by the cross-vendor review.**
2. **Identity-merge substrate (CA-CONN-09 done properly).** Not a one-shot heuristic: reversible
   merge events with provenance (`matched_by`, confidence, reviewer, timestamps), deterministic
   mailbox-owner self-mapping, HITL review queue for ambiguous merges, and the PF-01 interaction
   rule (a merge must never silently WIDEN a principal's access). The schema already allows many
   emails per person; the resolver deliberately never merges (839 persons ↔ 839 emails, owner
   split across identities). Biggest retrieval-correctness lever — a small model cannot recover
   from "everything from person X" being split 4 ways.
3. **`content_item` + `chunk` lineage contract** (schema design; population comes with Ask).
   Every chunk: org_id, source type/row/connection, visibility binding, source content hash +
   version, parser/cleaner/chunker/embedder versions, timestamps, entity links, extraction state,
   erasure + disconnect behavior. **Content-addressed at the `content_item` level: 59% of
   attachment rows (5,004/8,454) are byte-identical duplicates — extract once per (org,
   content_hash), chunk/embed once per content, or embedding spend and search results duplicate
   accordingly.** The tag taxonomy schema (§5) is designed in this same pass.
4. **Benchmark harness + answer-packet contract.** Gold question set over the dev corpus (seed it
   from the v2 harness's question bank — the research digest references a 162-question set from
   that context — then extend with corpus-specific questions); graders
   are DETERMINISTIC for numbers/dates/IDs/enums/evidence (v2's substring grading inflated small
   models); scored dimensions separated (intent, entity resolution, retrieval recall, calculation,
   evidence fidelity, wording, empty/unauthorized behavior, leakage, freshness). Architecture
   ablations built in: tools vs views, dynamic aggregates vs projections, before/after identity
   merge, lexical vs vector vs hybrid.

## 4. P1 — built WITH the Ask layer

5. **Typed query tools**: `resolve_person` (trgm+unaccent fuzzy, exposes ambiguity — never a
   silent ILIKE pick), `search_content`, `get_thread`, `get_person_activity`,
   `get_company_activity`, `get_counterparties`, `fetch_evidence` — plus the guarded read-only
   SQL hatch (RLS makes it safe by construction; one repair pass on error — measured "near-free
   win" for small models).
6. **Projection tables (async workers)**: `conversation`/`conversation_member` (versioned graph
   projection — exact References/In-Reply-To edges, phantom ancestors preserved, recompute on
   late-arriving mail, algorithm version + confidence, plus message-position/reply-depth/
   thread-span features as columns; NEVER an immutable thread_id column, and
   membership never widens visibility); pairwise **relationship edges** (person↔person/company,
   counts + first/last seen — "who talks to whom" as a one-table read); **entity dossiers** as
   versioned, WINDOWED metrics (`inbound_count_90d`, refresh stamps — never undefined all-time
   scalars) whose visibility semantics come from PF-01.
7. **Ingest enrichment worker (the "LLM at the entrance", raw + refined)**: an **async,
   replayable projection** behind ingest (evidence lands durably FIRST; refinement re-derives
   from evidence on prompt/model upgrades — never re-ingests). Bounded per-item tasks: language
   ID (per-span: bg/en/mixed/und + confidence), tag classification (§5), canonical dates,
   mention extraction. **Security property #1 (study §5): email is ADVERSARIAL input — the
   worker runs as a sandboxed, tool-less, no-authority, schema-constrained extractor, and every
   output is an UNTRUSTED claim by construction** (prompt injection cannot gain anything to
   hijack). Outputs live in the study's four trust zones (evidence → deterministic projections →
   probabilistic claims → HITL-accepted knowledge) under the full refined-artifact contract:
   span-level lineage, input-hash idempotency, model+prompt+schema versions, **bitemporal**
   observed/effective time, confidence + proposed/accepted/rejected/superseded state,
   contradiction links, inherited (intersection, deny-on-unknown) audience, reverse lineage for
   erasure. **LLM prose summaries: lazy-first, labeled, NEVER embedded into retrieval indexes,
   never authoritative on the high-frequency path** (study D3 critique adopted — stricter than
   this plan's first draft); promotion is eval-gated. **Founder sequencing rules (2026-07-05):**
   LLM refinement is the LAST lever — it enters only after the deterministic DB-only
   configuration is exhausted and its measured plateau frozen as baseline (EXP-001 gates this);
   and when it enters, it works **coarse-first: whole-THREAD refinement before per-email** —
   single-email refinement is the end-of-ladder fallback, not the default unit. **Open policy
   decisions: local model (Gemma-class, sovereignty-clean) vs EU/ZDR cloud for PRODUCTION
   (experiment-side: reader local / enrichment on Together accepted for the founder corpus);
   raw-retention (encrypted source bytes + TTL vs accept-lossy — today's "raw" layer is already
   lossy: attachment bytes are discarded at ingest).**
8. **Chunking + embeddings + hybrid retrieval** — defaults per the study's contested-decisions
   table (§6 there): quote-strip preserving authored AND quoted spans with offsets → ~512-token
   chunks / 64 overlap; **bge-m3 local multilingual (BG+DE+EN), 1024-dim `halfvec`, cosine**
   (never a cloud embedder — sovereignty); **exact/flat scan first, HNSW only when measured
   latency demands** (~1–2M chunks/tenant); dense + keyword + RRF fusion; FTS on chunks with
   `simple` config; **content-once + occurrence rows** with refcounted erasure (§3.3 — a shared
   content vector dies only with its LAST occurrence; body chunks cascade 1:1 with their email;
   a name in another person's surviving mail is redact + delete-and-recompute, never a
   column-scrub that leaves the vector encoding it). Per-actor **reply-obligation** projection
   (reason + evidence — never a thread boolean) joins the projection set.

## 5. Tags — controlled three-tier taxonomy (schema P0, worker P1)

Closed vocabulary the guard LLM classifies INTO (multi-label + confidence) — never free
generation (tag soup: `invoice`/`Rechnung`/`фактура` fragmentation fails silently, worst for
small models). Deterministic rule-tags first (meeting invites, invoice-shaped attachments,
automated mail) — LLM only for semantic tags. Tiers: **core** (~15–20 universal, Ethera-owned) →
**industry packs** (opt-in at org onboarding in the platform console; versioned reference data;
additive; key renames are migration events, adoption always explicit) → **org extensions**
(HITL review queue: the LLM proposes, a human approves — the clari-pulse moment). Tag keys are
stable identifiers; English canonical keys, localized display. No cross-tenant taxonomy learning
(editorial curation only — §7/GDPR). Schema: `tag` (tier, pack_key, pack_version) +
`org_tag_pack` + `content_tag` (content ref, tag key, confidence, source rule|llm|human, model
version) — RLS'd, GIN-indexed, erasure-hooked, visibility-inherited (a sensitive tag on a private
email is sensitive metadata).

## 6. P2 — only on benchmark evidence

Daily aggregate fact tables (if dossier windows prove too slow), a real BM25 extension, OCR for
the 107 `scanned_pending_ocr` PDFs (already tracked in CA-CONN-04 Phase C), pptx/legacy
extractors (185 `unsupported_format`), Postgres 17/18 upgrades (JSON_TABLE, virtual generated
columns, uuidv7) as convenience.

## 7. Ingest performance track (separate lane, measured 2026-07-03)

End-to-end **2.6 emails/s** (5,909 emails / 38.5 min): extraction 188ms/email (48%) · DB round
trips ~120ms (31%) · parse 45ms (11%) · file read 37ms (9%), all strictly serial. At this rate a
100k-email mailbox first-sync ≈ 11h. Levers, in ROI order:

1. **Content-addressed extraction** (§3.3) — **SHIPPED 2026-07-04 (pending commit)**: ingest
   reuses the newest attempted extraction per (org, content_hash, content_type) — via the
   fail-closed SECURITY DEFINER `email_prior_extraction` so person-less ingest keeps the win
   under the PF-01 visibility policies; provenance copied verbatim so version-aware backfills
   still target old engines. Tests in `test_email_ingest_attachments.py`.
2. **Per-run resolver cache** — **SHIPPED 2026-07-04 (pending commit)**:
   `entities/services/resolution_cache.py`, transaction-aware (staged entries promote on commit,
   discard on rollback — savepoint-depth-tracked, since RELEASE SAVEPOINT fires after_commit in
   SQLAlchemy 2.x+asyncpg); skips repeat person/company lookups, alias/link/host savepoint
   attempts, and covered seen-window UPDATEs; race-safe get-or-create untouched underneath.
   Tests in `tests/entities/test_resolution_cache.py`.
3. **Batch child-row flushes** (recipients/attachments flush per row today) + pipeline stages
   (parse batch N+1 while committing N; fetch-ahead one batch in the production runner).
4. **Parallel mailboxes** at fleet scale — connections are independent; the resolver is already
   documented race-safe. Extraction parallelism (process pool) only if 1–3 prove insufficient.

Keep: one-commit-per-email resumability; sequential folders per connection (rate-limit
courtesy); CPU off the event loop (already `asyncio.to_thread`).

## 8. What we explicitly rejected (and why it stays rejected)

Raw text-to-SQL for small models (benchmark-refuted); query-time multi-hop synthesis by small
models (benchmark-refuted); materialized views for model-facing data (no RLS in PG16); an
immutable `thread_id` column on email_message (late mail recomputes threads); all-time rollup
scalar columns ("top counterparties" depends on window/visibility/merges); a graph database at
this scale (edge tables + recursive SQL suffice); a generic LLM answer-store (stale derived PII,
erasure burden); free-form LLM tagging (tag soup); `COMMENT ON` as a pillar (kept as cheap
hygiene — the measured lever is descriptions IN the tool schema serialization); heavyweight
schema pruning for the SQL hatch (high-recall focusing + repair loops instead).

## 9. Reconciliation with the parallel panel study (binding resolutions)

`docs/connect-memory-db-optimization-study.md` (same date, independent 26-agent panel + Codex
pass) converges on the thesis and independently verified the same live numbers. Three conflicts,
resolved:

1. **`thread_id` column** — the study materializes `email_message.thread_id`; the Codex review
   behind this plan rejected an IMMUTABLE thread id. Resolution: the versioned `conversation`
   projection is the record; a **recomputable** `thread_id` pointer column (re-stamped when
   late-arriving mail merges components, algorithm-versioned) is permitted as a read
   optimization. Both constraints hold.
2. **LLM summaries** — the study is stricter than this plan's first draft (never embedded, never
   authoritative, lazy-first, eval-gated). **The stricter rule is adopted** (see §4.7): a weak
   model's prose must not freeze into un-erasable authoritative truth, and regeneration re-leaks
   erased PII into fresh vectors.
3. **PF-01 sequencing** — the study specifies per-occurrence visibility mechanics but does not
   sequence PF-01; this plan's P0 gate stands (it is EPIC-PF-01's own rule: no retrieval table
   exists un-scoped).

Unique to the study (adopted, not duplicated here — read it for mechanics): the four trust
zones + refined-artifact contract, the prompt-injection sandbox property, the raw-retention open
decision, per-actor reply-obligation, the query-workload rubric, embedding/erasure defaults with
flip conditions, and the eval-harness parameters (150–300 labeled BG/EN questions measured on the
actual 7–8B sovereign reader). Unique to this plan: the PF-01/identity-merge/lineage sequencing
gates, the typed tool/semantic layer detail, tags + industry packs (§5), the performance lane
(§7), and the rejected-alternatives record (§8).

## 10. Sequencing snapshot

```
NOW (this plan's sign-off)
 └─ P0.1 PF-01 ✅ BUILT 2026-07-04 (0019 + access module; residuals = FIX_BEFORE_PROD PF-FBP-*)
 └─ P0.2 identity-merge substrate           ├─ then → P1 Ask layer (chunks/embeddings/tools/
 └─ P0.3 content_item+chunk+tag schemas     │          projections/enrichment worker)
 └─ P0.4 benchmark harness + contracts ─────┘         └─ P2 on benchmark evidence
Performance lane (§7): items 1–2 ✅ SHIPPED 2026-07-04; 3–4 open.
```
