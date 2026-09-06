# Memory DB Optimization Study — "smart DB, small model"

> **Status re-check (2026-09-06):** the §1 ground-truth measurements still hold exactly — email dedup
> 0.17% redundant by `message_id`, attachment duplication 59.2% (3,450 distinct `content_hash` of
> 8,454), `language` 100% NULL (0 of 5,893), `person_email` strictly 1:1 (839/839) — measured
> 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3). **Nothing from §9 Phase 1+ has been
> built:** pgvector 0.8.2 is installed and **zero columns anywhere are vector-typed**; there is no
> chunk store and no embedding lane. Of the Phase-0 spine, identity-merge and the language column are
> still open (the two numbers above); the scalar/typed tool layer was built instead as the Ask layer
> (`backend/app/ask/`, `docs/PM/ask/ASK-01-ask-layer-architecture.md`) and is uncommitted working-tree
> code (`…built-vs-docs-map.md` §4, group A). **Read alongside two later documents that cover the same
> ground:** `docs/PM/ask/ASK-02-small-model-to-100-safe.md` (2026-07-06, why the small reader plateaus)
> and `docs/PM/memory/MEM-01/` (2026-07-26, the later memory design, self-described as a design draft
> pending EXP-003). Neither names itself this memo's replacement; whether MEM-01 supersedes §4/§9 of
> this memo is not recorded anywhere and is a founder call.
>
> **Status:** design discussion / decision memo. **No schema or code was changed.** Produced 2026-07-04.
> **Method:** a 5-design adversarial panel (Claude, 26 agents) + a cross-vendor pass (GPT-5.5 / Codex) +
> direct verification against the live dev DB. Every agent claim was treated as unproven and checked
> against source/data before landing here.
> **Purpose:** lock the DB/architecture *before* we build chunking + embeddings, so the foundation the
> semantic layer sits on is the right one.

---

## 0. The thesis (why this study exists)

Design the database so that **even the smallest local LLM maxes out results.** Assume the reader model at
query time is *weak* — a 7–8B, on-prem, EU-sovereign model (no frontier API, for GDPR/DACH data
sovereignty). The governing principle:

> **Maximize the ratio of (intelligence baked into the DB at write-time) to (reasoning required from the
> model at read-time).** The model should **retrieve and reword**, never **derive and synthesize.**

This is strategic, not cosmetic: if a small model wins, One AI runs fully sovereign on-prem in the EU. The
prior v2 build benchmarked small models at **0–7%** — that failure was the DB asking the model to think at
read time. This whole study is about not repeating it.

---

## 1. Ground truth — verified live numbers (not the seeded assumptions)

The most important result of the study is a **premise correction**. The working assumption ("~39% of
emails are stored duplicates", from the 2026-06-10 audit) is **stale**. I queried the live dev DB
(`one-ai-mvp-db-1`, org `d1500000…0001`, 5,893 emails, one connection) directly:

| Claim under test | Verified measurement | Verdict |
|---|---|---|
| "39% of emails are stored duplicates" | redundant rows by `message_id` **0.17%**; by `(from,subject,sent_at)` **0.29%**; by identical `(subject,body)` **1.95%** | **REFUTED** — email dedup is effectively solved (`dedup_key` content-identity works) |
| Real dedup opportunity is **attachments** | **5,004 / 8,454 attachment rows (59%)** are byte-identical dups of another row (`content_hash` shared) | **CONFIRMED** — the actual dedup/extraction win |
| `language` is unpopulated | **5,893 / 5,893 NULL (100%)** | **CONFIRMED** — the single biggest missing write-time signal on a BG+EN corpus |
| `person_email` is effectively 1:1 | 839 persons / 839 emails / **max 1 email per person** / **276 (33%) null `display_name`** | **CONFIRMED** — entity **resolution**, not email dedup, is the graph-quality gap |
| Opaque attachments | **940 `ms-tnef` (all `pending`) + 107 OCR-pending + 185 unsupported ≈ 1,230** content-opaque | **CONFIRMED** |
| Thread signal | ~**68%** of emails carry `in_reply_to`/`references` | **CONFIRMED** — strong raw material for write-time thread assembly |

**Two facts that reframe everything, both confirmed in source code:**

1. **There is no true "raw" layer today.** `_store_attachments` drops attachment bytes after extraction;
   `email_message` stores no RFC822 bytes. What the plan below calls a "raw chunk" is **already lossy
   decoded text.** Retaining genuine raw is a *deliberate change*, not a freebie (see §5).
2. **The resolver never merges.** `entity_resolver._get_or_create_person` looks a person up *by email* and
   inserts a new `Person` + one `PersonEmail` for every unseen address — so one human with two addresses
   fragments into two person rows. `person.id` is *already* the canonical identity; the schema *already*
   allows many emails per person. The resolver simply never exploits it.

**So "fix dedup before embed" is redirected:** email-body dedup is done. The pre-embed work is
**(a) attachment content-dedup (~60%), (b) entity-resolution / person merge, (c) populate `language`,
(d) quote-strip + a content/occurrence split.** Only one mailbox is connected today, so "same mail across
mailboxes stores twice" is a *future* risk, not a present one.

---

## 2. The query workload — the rubric everything is judged against

Small-model optimization targets the **common case**. The high-frequency mass of real questions:

- **Temporal:** "latest email from X", "last thing the CEO said to me", "from last week"
- **Point-lookup:** "who is this / their address / their company / internal or outside?"
- **Multi-hop (the daily killer):** "which unanswered emails still need my reply?"
- **Fuzzy-semantic (needs the unbuilt layer):** "the email where someone complained about the delay",
  "what did we decide about pricing?"

The catalog tags each query by **what a small model is bad at** — multi-hop, aggregation,
temporal-ordering, dedup-vs-distractors, entity-disambiguation, cross-source-join. **The design job is to
move every "bad-at" query to write-time structure.** Three unbuilt structures gate ~⅓ of the workload:
(a) the **semantic layer** (vectors + multilingual keyword + fusion), (b) **fact extraction**
(commitments / amounts / dates), (c) **cross-connector fan-in** beyond IMAP.

The already-won baseline proves the strategy works: `direction`, `is_reply`, `is_internal`,
`has_attachments`, `first_seen_at`/`last_seen_at` already turn several questions into scalar reads. Every
new structure must clear that floor.

---

## 3. The five designs, and how each fared under attack

Five genuinely different philosophies were each proposed in full and attacked by three adversarial lenses
(small-model realist · ops/cost/staleness · security/GDPR/messy-data).

| # | Design philosophy | Unique contribution kept | Fatal / serious critique |
|---|---|---|---|
| **D1** | **Contextual chunk-centric** — one rich chunk store, contextual prefix, hybrid vector+keyword+RRF | The chunk as the self-describing unit; a **deterministic (templated, non-LLM) context prefix**; hard-filter columns baked into the chunk | Per-chunk LLM prefix too costly/un-erasable; a canonical-only-embed gate loses per-source permission; `created_at` partitioning is illegal-with-unique |
| **D2** | **Graph-first (GraphRAG)** — entities + typed weighted edges as primary substrate; embed node/community summaries | **First-degree deterministic edges** (`corresponds_with`, employer, thread-membership) — free byproducts of resolution | Leiden communities + LLM community summaries serve only the low-freq tail (YAGNI); materialized `co_recipient` is O(recipients²); summary-only embedding kills BG keyword recall |
| **D3** | **Precomputed-summary read-models** — write-time digests/profiles the model reads whole | **The structured-scalar spine** (precomputed `awaiting_reply`, latest-pointer, rollup counts) — every critique conceded this is the strongest single idea | Embedding LLM prose freezes a *weak* model's output as authoritative + creates an un-erasable PII reservoir → **reject the embedded prose, keep the structured fields** |
| **D4** | **Structured-fact extraction** — typed facts (amounts/dates/commitments) so aggregates become SQL | **Deterministic extractors** (amounts, dates, phone/title) + the **provenance pillar** (every fact row → `source_email_id` + verbatim span + confidence) | LLM-extracted facts with a citation are *more* convincing when wrong; a per-doc `mentions` table is a 100M-row trap |
| **D5** | **Retrieval-interface ergonomics** — optimize the tool payloads the model literally sees | **The payload contract** (≤~400 tok, best-first, self-describing, provenance-tagged, returns *candidates* not a silent top-1) + RLS-bearing real tables over materialized views | An ML "intent-router" is *more* fragile than the model it protects → replace with a small fixed tool set + deterministic server-side resolution |

**The convergent verdict:** no single design wins; the answer is a **layered composition** — but a
*decided* one, not a union.

---

## 4. Recommended architecture (the reconciled target)

A **deterministic write-time spine** that converts the high-frequency mass to scalar reads with zero
semantic layer, under **one hybrid semantic store** for the fuzzy tail, with **LLM work deferred, gated,
and async**. The read interface is a **small fixed tool set** that resolves entities/dates server-side and
**returns candidates, never a confident top-1.**

```
Evidence (immutable)         ──►  source records; (decision) retain raw bytes encrypted + TTL
        │
Deterministic projections    ──►  canonical identity (merge on person.id), threads,
  (synchronous, always fresh)      authored/quoted spans, language, content-hash dedup,
        │                          first-degree edges, scalar rollups  ← HIGH-FREQ MASS SERVED HERE
        │
Hybrid semantic (async)      ──►  content/occurrence chunk store: embed DISTINCT content once
        │                          (bge-m3), keep every occurrence for visibility/erasure;
        │                          dense + keyword + RRF (+ optional local reranker)  ← FUZZY TAIL
        │
Probabilistic claims (gated) ──►  LLM-extracted commitments/decisions: proposed → HITL → accepted;
                                   never silently promoted; labeled index, never blended as evidence
```

**Taken / rejected across the panel:**
- **Take** the structured-scalar spine (D3), the deterministic extractors + provenance pillar (D4), the
  self-describing chunk + deterministic prefix + hard-filter columns (D1), the first-degree edges (D2),
  the payload contract + candidates-not-top-1 (D5).
- **Reject** embedding LLM prose/summaries (re-leaks erased PII on regeneration, freezes a weak model's
  output as truth); the ML intent-router; live per-write rollup counters (hot-row contention on the
  owner/internal-company rows — recompute on a covering index instead); Leiden communities and
  `co_recipient` materialization (compute the rare intersection on demand).

---

## 5. Your idea — the entrance-LLM + raw/refined layer (elevated to the governing frame)

Your instinct ("an LLM at the entrance that preprocesses incoming data; keep it raw but also a refined
sector") is the **correct meta-architecture** — it is the *mechanism* that makes "smart DB, small model"
real. It's the **medallion pattern** (raw → refined) with an LLM as the transform, and it already exists in
miniature in your own `email_attachment` table (`extracted_text` + `extractor_name`/`extractor_version` —
raw+refined+provenance). Both advisors endorsed it. **Two corrections make it safe:**

**(a) Not synchronous "at the entrance" — an async, replayable projection.** Ingestion must *durably land
evidence first*; probabilistic refinement runs *after*, and is **regenerable from evidence** (so when you
improve a prompt/model you re-derive, not re-ingest). This is exactly why you keep raw — raw is the master
tape; refined is a disposable projection.

**(b) Four trust zones, never blended:**

| Zone | Contents | Trust |
|---|---|---|
| **Evidence** | source bytes (where policy permits) + canonical parsed records | ground truth |
| **Deterministic projections** | identity, threads, spans, hashes, **language**, dates, exact extractors | trusted, re-derivable |
| **Probabilistic claims** | LLM-proposed facts (commitments, decisions, intents) | **untrusted** until verified |
| **Accepted knowledge** | rule-verified or **human-approved** claims (HITL) | trusted, provenance-bound |

**The non-negotiable contract for every refined artifact** (the rule that makes this safe rather than
dangerous — a write-time error becomes *trusted* refined truth the small model can't second-guess):
span-level lineage to source · input-hash + idempotency key · producer/model + prompt + schema + pipeline
**version** · generated-time + **valid-time** (bitemporal: observed vs effective) · confidence +
`proposed/accepted/rejected/superseded` state · contradiction links · **inherited access scope** · reverse
lineage for **erasure**. A claim combining sources inherits the **intersection** of their audiences
(unknown ⇒ deny — see the repo's `EPIC-PF-01` permission-fidelity epic). No free-form LLM summary is ever
embedded; accepted atomic claims get a **separate, visibly-labeled** retrieval index, never mixed in as
indistinguishable evidence.

**When is write-time LLM work worth it?** When the result is *repeatedly queried, stable,
schema-constrained, evidence-citable, and measurably helps the weak reader* (commitments, decisions, intent
tags). Leave low-frequency synthesis, volatile summaries, and speculative merges **lazy**. Policy:
**lazy-first, promote to eager once query frequency justifies it.**

**The blind spot this frame forces you to confront — email is adversarial input.** The entrance LLM
processes attacker-controlled content (email bodies, attachments) → **prompt injection.** It must run as a
**sandboxed, tool-less, no-authority, schema-constrained extractor**, and its outputs are **untrusted
claims** by construction. This was missed by the entire Claude panel and is the single most important
security property of the entrance-LLM design.

---

## 6. Contested decisions (default · why · what flips it · phase)

| # | Decision | Recommended default | Flips if | Phase |
|---|---|---|---|---|
| a | Fix dedup/resolution before embedding? | **Yes for resolution + language + attachment content-dedup.** Email dedup is already done; no fuzzy pre-embed gate | broadcast/newsletter-heavy tenant makes embedding identical bodies costly → per-connection (never org-wide) canonical gate | 0 |
| b | Chunk size + contextual prefix | quote-strip → ~512 tok / 64 overlap (median body = 1 chunk); **short deterministic templated prefix**, heavy context in **columns** not the embedded string | eval shows prefix dilutes discrimination on thin bodies → drop from embedded text, keep in keyword + columns | 1 |
| c | Embedding model + index | **bge-m3, local, multilingual (BG+DE+EN), 1024-dim `halfvec`, cosine.** Not OpenAI (cloud egress kills sovereignty) | GPU-less box → smaller/quantized embedder or async backfill; add bge-m3 **sparse/multi-vector** if BG recall underperforms | 1 |
| c′ | ANN index | **Exact/flat scan first** (tens of thousands of chunks = ms); add **HNSW only when measured latency requires it** (m=16, ef_construction=200) | corpus crosses ~1–2M chunks/tenant | 1→later |
| d | Write-time summarization | **Minimize.** Structured scalars synchronous; **no LLM prose embedded or served as authoritative** on the high-freq path; genuine prose lazy + `prompt_version` + TTL | labeled eval shows the local summarizer clears a precision bar **and** "full-picture" demand is high | 3 |
| e | Which graph edges | **Build first-degree deterministic edges** (`corresponds_with`, employer, thread-membership). Reject communities + `co_recipient` materialization | relationship-analytical tail becomes high-freq → materialize 2-hop intersections on demand | 0 / never |
| f | Prose → typed facts | **Deterministic facts eagerly** (amounts, dates, phone/title) with provenance. **LLM facts built but gated** (confidence + HITL + snippet), not authoritative until measured | commitment-extraction precision ≥ ~0.9 on a labeled BG+EN set → promote | 2 / 3 |
| g | Read interface | **~6 fixed disjoint-by-shape tools** (`who_is`, `recent_with`, `thread_of`, `awaiting_reply`, `count_over`, `search_memory`); server-side `resolve_entity`/date-normalize returning **candidates + why + confidence**; `search_memory` is the graceful fallback. **No ML router** | measured misroute > distractor-error on an enlarged `search_memory` top-k → collapse toward one `about(entity)` hero tool | 0 / 1 |
| h | Partitioning | **None** for the single-tenant on-prem flagship until ~1–2M chunks; then **hash/list by `org_id`, never `created_at`.** Multi-tenant SaaS → per-org partial indexes early (avoid shared-HNSW recall starvation) | tenant scale crosses threshold | 1→later |
| **i** | **Reply-obligation model** | **Per-actor obligation with reason + evidence**, NOT a thread boolean (a thread has FYIs, automated mail, colleague replies — a boolean generates confidently-wrong task lists) | — | 0 |
| **j** | **Content vs occurrence** | **Embed distinct content once** (keyed by content-hash + extractor/chunker version); keep **every occurrence** for visibility/permission/erasure. Body chunks stay ~1:1 (plain cascade); **shared content is refcounted** — its vector dies only on last-occurrence erasure (see §8.6). Exact content-dedup before embed is safe; fuzzy dedup is not | — | 1 |
| **k** | **Eval harness** | **Build it first (Phase 0).** 150–300 real BG/EN questions with gold answers, measured on the actual 7–8B reader (recall@k, answer accuracy, ambiguity preservation, citation correctness, permission-safe recall, temporal accuracy, unsupported-claim rate) | — | **0 — the governing artifact** |

---

## 7. Cross-vendor reconciliation (where GPT changed the answer, verified against source)

| GPT challenge | Verified? | Resolution |
|---|---|---|
| Don't add `canonical_person_id`; schema already allows many emails/person; resolver just never merges | **CONFIRMED** (`entity_resolver.py:149–198`, live 1:1 data) | Merge onto existing `person.id` + a **reversible identity-assertion / merge-history ledger**; drop the new-column idea |
| The plan's "raw" is lossy — bytes discarded at ingest | **CONFIRMED** (`email_ingest_service.py:174`, `email.py`) | Decide explicitly: retain **encrypted source bytes under tenant TTL**, or accept no re-extraction/OCR-recovery. Don't call decoded text "raw evidence" |
| `awaiting_reply` as a thread boolean is semantically false | Sound (design) | Adopted as decision (i) — per-actor obligation with reason + evidence |
| `tsvector`/GIN is **not** BM25; bge-m3 sparse/multi-vector unused | Accurate | Treat the keyword arm honestly: either a real BM25 extension (ParadeDB / VectorChord-BM25) or accept `ts_rank` + lean on bge-m3 sparse. **Open decision.** |
| Benchmark **exact scan before HNSW** at this scale | Sound | Adopted — decision (c′) |
| Identity merge = probabilistic, reversible evidence, not one-shot cleanup | Sound | Adopted into (a) + the assertion ledger |
| Entrance-LLM must be **async replayable**, not synchronous | Sound | Adopted — §5(a) |
| **Email is adversarial input** (prompt injection) | Sound, **major miss** | Adopted — §5 blind spot; sandboxed tool-less extractor, outputs = untrusted claims |
| **Bitemporal** facts (observed vs effective time) | Sound | Adopted into the §5 contract |
| **Eval harness is the governing architecture**, not the vector index | Sound, **strongest single point** | Adopted — decision (k), elevated to Phase 0 |

Where the two advisors **agreed** (stronger signal): embed **raw/authored chunks, never LLM summaries**;
deterministic-before-LLM; precision > recall; candidates-not-silent-top-1; GDPR erasure must traverse
lineage and delete/recompute derived vectors, edges, summaries, caches.

---

## 8. What must be fixed first (Phase 0, before a single vector is written)

0. **Eval harness** — 150–300 labeled BG/EN Q&A on the real 7–8B reader. Build it **in parallel** with the
   deterministic spine (1–7 below are unit-testable on their own); it gates **retrieval-param tuning
   (Phase 1)** and **LLM-fact promotion (Phase 3)**, not the whole build.
1. **Identity merge** → repoint onto existing `person.id`; attach multiple `person_email`; reversible
   assertion ledger; fix null `display_name` where a named sighting exists; fix the role heuristic both ways.
2. **Thread materialization** → `thread` table + `email_message.thread_id` (chain-walk `references`/
   `in_reply_to`); **per-actor reply-obligation** with reason + evidence (per-connection scope, flag
   cross-connection uncertainty honestly).
3. **Language detection** → populate `language` (100% NULL today) with a local detector, **per-span**
   (`bg`/`en`/`mixed`/`und` + confidence), at write. It is a hard filter, a prefix field, and a precision lever.
4. **Content/occurrence split + attachment dedup** → quote-strip (preserve authored *and* quoted spans with
   offsets); content-addressed blob/extraction keyed on `content_hash` (the 59% win); occurrence rows carry
   per-source visibility.
5. **Embed-quality gates** → don't embed the ~1,230 opaque (TNEF/OCR-pending/unsupported) or empty rows;
   honest NULL. Repair mojibake where feasible; TNEF is the largest opaque type — decide extract vs skip.
6. **Erasure contract baked into the chunk schema before embedding.** Split by content type, because §6(j)
   embeds shared content once — a uniform single-`email_id` cascade would be *wrong* for the 59% attachment case:
   - **Body chunks** are ~1:1 with their email → `(org_id, email_id) ON DELETE CASCADE` drops the vector with
     the row (the clean single-cascade story).
   - **Shared content** (deduped attachments — the 59% case — and forwarded/quoted spans) is content-once +
     **refcounted via the occurrence table**: the content vector is deleted only when its **last** occurrence
     is erased.
   - A subject's name sitting in **another** person's surviving body is reached by a bounded keyword lookup →
     redact + **delete-and-recompute** the affected chunk (never a column-scrub leaving the vector encoding
     the name).
   - No LLM prose is embedded (regeneration would re-leak the name into a fresh vector).
7. **Raw-retention decision** (§5): keep encrypted source bytes + TTL, or accept lossy. Pick one on purpose.

---

## 9. Phased build order

- **Phase 0 — deterministic foundation + eval harness, ZERO embeddings.** Items above + first-degree edges
  + the scalar tools. Converts the high-freq mass to scalar reads. Highest leverage, lowest risk. *(Ordered
  first because embeddings built on a fragmented, thread-less, language-less graph must be thrown away.)*
- **Phase 1 — hybrid semantic Layer-2.** content/occurrence chunk store → bge-m3 `halfvec` → exact scan
  (HNSW later) + keyword + RRF (+ optional local reranker); async embed lane; read-time collapse; the
  `search_memory` tool. *(Consumes Phase-0 resolution/threads/language.)*
- **Phase 2 — deterministic facts.** amounts/dates/deadlines/phone/title + DuckDB over the typed xlsx grids.
  Safe, provenance-backed, no LLM.
- **Phase 3 — LLM facts + lazy prose (gated, Learn boundary).** commitment/decision extraction behind
  confidence + HITL + measured precision; lazy on-demand dossiers; communities only if the tail proves
  high-freq. *(Escalating LLM dependence → last and gated; never blocks the high-freq win.)*

Ordering principle: **(leverage × certainty) ÷ risk**, descending.

---

## 10. Genuine open tensions (tie-breaker + the evidence that settles each)

1. **Embed-all vs content-once-plus-occurrences at scale.** Default content-once (permission/erasure clean,
   avoids duplicate top-k). *Evidence:* semantic-near-dup rate + bge-m3 throughput on the actual on-prem box.
2. **Does the deterministic prefix help or dilute?** *Tie-breaker:* ship whichever wins precision@5.
   *Evidence:* A/B embed with vs without prefix on a labeled cross-lingual (EN query → BG body) set.
3. **How capable is the sovereign nightly/extraction model, really?** Everything in Phase 3 rests on it.
   *Tie-breaker:* gate behind HITL until measured. *Evidence:* commitment/decision precision on a labeled
   BG+EN set; promote at ≥ ~0.9.
4. **One hero tool vs a small fixed set** (how much tool-choice a 7–8B can bear). *Tie-breaker:* if measured
   misroute > distractor-filtering error on an enlarged `search_memory` top-k, collapse toward `about(entity)`;
   never an ML router. *Evidence:* misroute rate of the actual local model across the six tools.
5. **Keyword arm: real BM25 extension vs `ts_rank` + bge-m3 sparse.** *Tie-breaker:* whichever wins BG recall
   in eval at acceptable ops cost. *Evidence:* recall on Bulgarian keyword queries.
6. **Raw retention: keep encrypted bytes vs accept lossy.** *Tie-breaker:* re-extraction/OCR-recovery value +
   forensic need vs storage + erasure-surface cost. *Evidence:* how often improved extractors would re-mine
   old attachments; regulatory retention posture.

---

## 11. The one-line takeaway

**Build the eval harness first; make the deterministic write-time spine (identity-merge, threads, language,
content-dedup, first-degree edges, scalar tools) carry the high-frequency mass with no model at all; embed
distinct *raw/authored* content once (bge-m3, local) with occurrences for permission/erasure; and treat the
entrance-LLM as an async, sandboxed, schema-constrained *claim proposer* whose every output is
provenance-bound, bitemporal, HITL-gated, and erasable — never blended into evidence.** That is how the
smallest model wins.
