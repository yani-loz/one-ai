# ASK-02 — Why the small model plateaus, and the architecture that takes it to ~100% *safe*

**Status:** diagnosis + design, evidence-backed (2026-07-06). **Reader arm:** `google/gemma-4-31B-it`
(bigger-model is off the table by founder decree). **Scope:** the Ask layer over the ingested email
corpus (5,893 emails, 8,454 attachments, 839 persons, bilingual BG/EN).

**Role:** the authoritative answer to "why can't the small model hit 100% when the data is there, and
how do we get there anyway." **Depends on:** the forensic autopsy of all 43 benchmark questions
(`Benchmarks/_ask_loop/`), the EXP-001 multi-arm forensics, two independent advisor passes (Claude 4.8 +
GPT-5.5/Codex), and a 5-agent adversarial design panel whose ACL/coverage claims were verified on the
live DB. **Key invariant:** every number here was verified against the running database or a real run
transcript; where a claim is a projection/estimate it says so.

---

## 0. TL;DR (one page)

**Why it plateaus.** The small model is not stupid and the data is not missing. The **tools force it to
do, in its head, work that small models provably cannot do** — rank/enumerate across thousands of rows,
join "between person A and B," compute reply-deltas, hold entity attribution straight, assemble a
timeline — and when it can't, **it guesses confidently instead of saying "I can't."** We proved the wall
is not bandwidth: giving the model **4× more context changed nothing** (0 new correct answers). A strong
reader (Opus) on the *identical* tools scores 36/43; our small model scores 10/43. The gap is
reader-competence at in-context reasoning, not retrieval.

**The honest target.** **100% autonomous-correct is impossible** — even Opus caps at 36/43 (~84%). The
right, achievable goal is **100% *safe disposition*: every question is either answered-correctly or
honestly escalated, with a near-zero confident-wrong rate.** That is the enterprise-trust metric, and it
is squarely reachable.

**How.** Move the computation **into the database** — the model only ever *renders* an answer the DB
already computed, and never invents a fact — and make **"I don't know / here's what I'd need" a
first-class answer.**

**The numbers (converged across both designers), split by confidence:**
- **Verified-solid (week 1, C1–C3): 10 → ~20/43 autonomous** — these are bug-fixes + wiring + a safety
  pass, and the safety floor alone converts *most* confident-wrongs to honest answers.
- **Projected-contingent (C4–C5): up to ~30–34/43 autonomous** — *contingent* on entity-resolution +
  signature-NLP + party-role quality (the projections the panel confirmed are the hard part; must be
  re-measured, not assumed). ~30–34 ≈ the Opus ceiling — you cannot beat it with this reader.
- **Safe disposition: ~40–43/43 (~93–100%)** — *by construction:* everything not deterministically
  verifiable is **escalated, not answered.** That's the guarantee.

---

## 1. Why it can't hit 100% — explained simple

### The one-sentence answer
> Our six tools are a *search box*. The questions need a *calculator, a spreadsheet, and a filing
> system*. We ask a small model to be all three in its head — and when it fails, nothing stops it from
> answering anyway with confidence.

### The decisive proof it is NOT a bandwidth problem
We reran the small model with **4× larger limits** (32K characters / 100 results instead of 8K / 50).
Result: **zero new correct answers, zero regressions.** More text neither helps nor hurts it. It is not
running out of room to think — **it lacks the machinery to compute.** (Corollary: spending on bigger
context windows or more turns is wasted money. The money goes into pre-compute.)

### The five plain-language reasons (each verified on the live DB)

1. **It can't count/rank/enumerate across the whole corpus.** "Which clients have the most email
   threads?" needs grouping thousands of rows. The model has no "rank all clients" tool, so it eyeballs a
   handful of company names it stumbled on and misses every real heavyweight (it named small accounts and
   missed Ocenki=466, the GBS group=~488, APIS, Data+). This is the single biggest failure family.

2. **It's ~90% blind to Bulgarian — silently.** "invoice" matches **40** emails; **фактура** matches
   **395** (verified). The model searches English, finds 40, and stops — because the auto-translate
   fallback **only fires when a search returns *exactly zero*.** A partial hit masks the truth. Every
   money/contract/expense question in a Bulgarian-dominant archive is exposed to this.

3. **A tool is literally broken for the most common document type.** "Do we have Word docs about X?"
   filters `content_type = "docx"` → SQL `ILIKE '%docx%'` → matches **0 of 811** real `.docx` files
   (their MIME type is `…wordprocessingml.document`, which doesn't contain the string "docx"). Same bug
   for xlsx/pptx. A perfectly correct question returns "no." **Pure bug, ~1-line fix.**

4. **The tools can't express "between A and B."** "All emails between Yani and Mihail about Vetera" is
   **exactly 72** in the DB — but the participant filter is **OR, not AND**, so the tool can't ask for
   *both*. The model tried to fetch emails one at a time, exhausted its 8 turns, and produced a partial
   list of ~10 with no count. (A 3-line pair-join returns 72 instantly.)

5. **It never says "I can't" — it fabricates.** *Every single failure is a confident wrong answer.* It
   stated "5,893 threads," "GBS has 6 people," "nothing went wrong with Kaufland," "the duration can't be
   calculated" (while both dates sat in its own answer) — all as fact. For a product sold on trust,
   **this is the real danger, bigger than any single miss.** In one sibling run the model *invented a
   "relationship expansion" out of a thread that literally contained a cancellation.*

### The decisive measurement: this is a *gathering* problem, not a *thinking* problem
For each of the 33 non-passing questions we asked: *was the needed evidence already in a tool payload
when the model answered wrong?* **Never-gathered 17 · Partially-gathered 12 · Had-it-but-miscomposed 4.**
So **~29/33 are a gathering deficit** — the tools couldn't express the operation, so the model never got
the evidence. That's why the fix is mostly *substrate* (compute it in the DB), with a *safety* layer for
the confident-wrong problem on top.

### A note of honesty: some of the ceiling is *definitional*, not competence
"How many threads do we have?" has no single right answer without pinning a threading algorithm (raw
messages 5,893; reply-roots 2,229; distinct-normalized-subjects 2,104; the gold wants ~2,962 — no simple
method even reaches its floor). Questions like this belong in **clarify/define**, not "model failure."

---

## 2. The evidence (how we know — not opinion)

**Method.** We ran a forensic autopsy of **all 43 benchmark questions**, fusing two artifact worlds
per question — the ask-loop's own cached Gemma transcripts (the target system) and the EXP-001 lab's
multi-arm forensics (the *same* questions answered by Gemma, Opus, and a SQL specialist on *identical*
tools, plus the judge's verifying SQL) — and verified every gold against the live database. Then a
5-agent adversarial panel (3 architects + 2 verifiers) designed and attacked the fix, with the
safety-critical claims (ACL isolation, projection coverage) **re-run as live SQL**.

**The reader gap (identical tools).** Gemma **10 pass / 5 partial / 28 fail**; Opus **36/43**. The tools
are not the ceiling for a *strong* reader — so the small model's problem is in-context reasoning, and the
cure is to remove that reasoning from its plate.

**The verified defects (all reproduced on the running DB):**
| Defect | Evidence | Fix cost |
|---|---|---|
| `docx`/`xlsx` content-type filter matches nothing | 0 of 811 real .docx | ~1 line |
| Bilingual search ~90% blind | invoice 40 vs фактура 395; fallback only on zero-results | small |
| No "between A and B" (participant filter is OR) | pair-join = 72, OR-filter = ~5,879 (whole corpus) | query-plan slot |
| No "oldest/earliest" path (search refuses without a date) | model blind-guesses windows from 1900 and times out | small |
| `counterparty_summary.total_mentions` double-counts | ranks apis at 22/wk vs true gbs 5.35/wk (CC-inflated) | replace metric |
| **Anti-bias smell: the shipped tool hardcodes the benchmark's BG glossary** | `shared_core.py:464` literally lists `оферта/договор/фактура/командировъчни/краен срок` | replace with general expansion |
| 6,000-char payload cap cuts **mid-JSON** | `agent_runner.py:208` slices the string | envelope fix |

The last two matter beyond a single question: the hardcoded glossary is the *exact idioms the answer key
rewards*, which **plausibly explains why dev (27%) beats holdout (13%)** — it's soft-tuned to seen
questions. Any real fix must generalize to an unseen tenant.

---

## 3. The honest target — 100% *safe*, not 100% autonomous

100% autonomous-correct is not on the table for *any* model here: Opus, the strongest reader we can run,
caps at **36/43 (~84%)** on honest infrastructure, with a genuine reasoning-bound residue (stakes
judgment, role-evolution, fuzzy-metric definitions). No small-model architecture answers all of that
autonomously.

What **is** achievable, and is the correct product metric:

> **100% safe disposition** = every request is resolved as one of **{answered-correctly, clarify,
> escalate}**, subject to a strict **near-zero confident-wrong** budget.

- **Autonomous-correct:** ~30–34/43 (≈ the Opus ceiling — the substrate lets a *small* model reach what a
  *strong* reader reaches, because the DB does the reasoning).
- **Safe disposition:** ~40–43/43 (~93–100%) — the rest are honestly escalated, not guessed.
- **The headline to sell:** *near-100% on fact-shaped questions, and near-zero confident-wrong on the
  judgment residue by escalating it. The unsupported-claim rate is the real number.*

**Where the "near-zero confident-wrong" guarantee actually bites — be precise.** It holds for
**typed/templated answers** (counts, dates, entities, lists with known completeness) where L4's
deterministic checks compare a stated value to a computed one. It does **not** hold *by checking* for the
**synthesis/arc class** (relationship histories, "why did X happen") — completeness and attribution
there aren't locally verifiable without the gold. For that class the safety comes from **escalating, not
answering** — so those questions are booked as *safe-by-escalation*, **not** as clean autonomous wins
(e.g. a relationship-arc question that needs role-evolution synthesis routes to escalate, not to a
confident paragraph). "Safe ≈ 100%" is true *by construction*: anything not deterministically verifiable
is handed off. That is the feature, not a gap.

---

## 4. The architecture

Two independent advisors and the evidence converge on one shape. Below it is the shape **as corrected by
the adversarial panel** (three of the corrections change the design materially).

### 4.1 The principle
1. **The DB computes; the model renders.** Every hard operation (count, rank, enumerate, join, timeline,
   dedup, identity-merge) happens in SQL/projections that have **no competence ceiling**. The model's job
   shrinks to (a) map the question to a structured request and (b) render pre-computed, typed facts into
   cited prose. **It may never invent, rewrite, or re-attribute a factual atom.**
2. **Few tools, not many.** The small model's core weakness is *choosing among tools and orchestrating*.
   The rejected router (−6pp) proved every added tool degrades marginal questions. So we **keep the flat
   primitive set, fix it, and add exactly one compute hatch** — we do **not** ship a dozen capability
   tools. **Crucial reconciliation:** the projections in §4.2 are **reached *through* that single
   query-plan hatch** (the model names a `capability` inside the one plan it already fills), **not a new
   model-visible tool each.** The model never has to choose between "a relationship tool" and "a dossier
   tool" — there is one entry point; the capabilities live server-side. This is how we get the projection
   power without re-creating the router's tool-count fragility.
3. **Non-generative routing.** No single generative "pick the class" decision (that was the router). Use
   deterministic lexical rules + embeddings to *shortlist*, run cheap candidate plans, and select by
   result-shape invariants — never the weak model's exclusive authority.

### 4.2 The database read-model (the heart of the fix)

**The ACL rule — proven on live data, non-negotiable (it's One AI's foundational constraint):**

> **Never materialize a tenant-global aggregate.** *Reproduced by the author on the live DB (in a
> rolled-back transaction):* a principal granted **100 of 5,893** messages, reading a **materialized**
> rollup, saw **383 domains / 18,728 mentions — the entire org's counterparty graph**, including domains
> from the 5,793 messages they cannot read. The **live `security_invoker` view** handed the *same*
> principal only **61 domains / 309 mentions — exactly their visible slice.** So: materialize
> **per-message fact rows** (each carrying `email_id` + the full PF-01 visibility triple + the identical
> RLS policy), and **aggregate at query time through `security_invoker` views**. At this corpus size a
> whole-corpus GROUP BY runs in **29 ms**, so materialization buys nothing anyway — it only leaks.

**`message_party_fact`** — the one intentional bridge (a base table, RLS-gated):
`org_id, email_id, sent_at, party_kind{from|to|cc}, direction, address_norm, domain_norm, is_free_mail,
person_id?, company_id?, thread_key?, visibility_scope, origin_scope, container_id, resolver_version,
source_watermark, built_at`. Grain: one row per (message × party appearance). It kills the
cross-entity-join class and feeds every rollup. **`is_free_mail`** (gmail/yahoo/…) is load-bearing: a
free-mail address is a *person* identity, never a company — which is how you find the vendors hiding
behind Gmail.

**Capability views (all live `security_invoker`, never materialized):**
- **relationship_rollup** (per counterparty): `count_distinct(message_id)`, `count_distinct(thread_key)`,
  first/last contact + message ids, inbound/outbound, active-day-count. **Replaces the broken
  `total_mentions`.** Wins the ranking/enumeration family.
- **entity_dossier** (per person, computed over caller-visible facts): visible names/addresses/domains,
  visible counts, and a **role-from-signature** fact (title/company parsed from the person's most recent
  non-reply signature, carried as a typed claim with confidence + evidence id).
- **document_canon** (per content-hash): ranked by a **business-signal composite** (outbound +
  human-authored filename + business MIME + size floor), *not* by raw copy-count — pure ubiquity ranks
  the founder's personal deck (44 copies) above the real company deck (33). Surfaces conflicts, never
  silently picks.
- **party_role** (vendor/client) — **address-level, low-confidence, evidence-carrying**: invoice/payment
  polarity + message direction, free-mail excluded from any domain rollup. Explicitly a *hypothesis*, not
  a fact; ambiguous → escalate.
- **thread** is **not** a materialized fact. Thread questions are answered with an explicit, **versioned**
  method (`in_reply_to`/`references` forest only — never subject-merge) and always return the method +
  an uncertainty flag.

**Projection-poison guardrails (mandatory — a wrong projection fails on *every* question at once):**
every derived fact carries `resolver_version` + `source_watermark`; nightly **shadow-recompute vs
canonical**; **metamorphic tests** (union identities, entity substitution, polarity reversal,
date-window monotonicity, duplicate recipients, **cross-visibility ACL revocation**); and **every fuzzy
metric's definition is surfaced in the claim's `method` field** ("responsiveness = median inbound-reply
latency over window W"), so the answer states its own definition instead of hiding a silent choice.

### 4.3 The agent architecture (four layers)

- **L0 — Honest primitives (fix the tools; server-side; always-on).** Format→MIME resolver (docx bug);
  **deterministic, question-blind bilingual + morphological expansion that fires unconditionally** (not
  only on zero results, and **not** from a hardcoded glossary — from a general translation/embedding/
  co-occurrence source); a completeness envelope `{total, returned, complete, order, date_span}` on every
  list tool; an ascending/"oldest" path. *Wins the bilingual + format + oldest questions and is the
  cheapest work on the board.*
- **L1 — One compute hatch (typed query-plan → parameterized SQL).** The model fills a **typed slot plan**
  (`participant_groups` with `all_groups` semantics, `count_distinct` grain, sort, window) that compiles
  server-side to parameterized SQL over an allow-listed schema. This captures the compute wins (the "72"
  pair-join, max-by-size) **without** exposing free reader-SQL — the existing `sql_guard` is
  *statement-shape* control, not a query *policy* (it doesn't bound join cardinality, distinct-grain, or
  cost). Raw reader-SQL stays only as a shadow-eval / analyst-mode hatch. *~70% of the plumbing already
  exists* (`sql_guard.py`, `query_database`) — the unlock is trunk-wiring + an information_schema-
  generated (never hand-written) schema card.
- **L3 — Answer-IR + deterministic renderer.** Capabilities return **typed claims** (`subject_id,
  predicate, value, unit, time_scope, method, completeness, evidence_ids[], version`). A deterministic
  renderer turns claims into cited prose. **Required-claim *classes* are mandatory in the renderer, not
  model-selected** (an entity answer *must* emit internal/external + org + role when those claim types
  exist) — otherwise "the model may order claims" silently re-opens the omission hole. The model may
  order claims for dossiers/timelines only; it never introduces a name, number, date, or polarity.
- **L4 — Symmetric verifier + abstention.** Deterministic gates: stated-number == computed-number; every
  named entity ∈ evidence set; every `[id]` ∈ payload; attribution/arc-role; **capability-driven
  completeness** (the capability returns candidate-set size + a complete flag; the answer must have
  considered the full set); polarity lock; **ACL recheck at render time**; cross-plan agreement;
  freshness. Then disposition: **answer / clarify / escalate**. The verifier is **symmetric** — it also
  *kills unjustified abstention*: when the answer is deterministically derivable (folder-absent ⇒ zero;
  two anchor dates in hand ⇒ subtract), it **forces the answer** instead of letting the model punt. This
  is what drives confident-wrong toward zero *without* over-escalating.

---

## 5. The action plan (ranked by return-on-effort)

The build sequence below is the surgical designer's, cross-checked against the coverage verifier. Effort
is engineer-days; "gain" is autonomous-correct questions unless marked *safe*.

| # | Build | What it is | Gain | Effort |
|---|---|---|---|---|
| **C1** | **L0 honest primitives** | docx/MIME fix, always-on general bilingual expansion, completeness envelope, "oldest" path, mid-JSON cut fix | **+5** (V012 V037 V039 V041 V004) | 1–2 d |
| **C2** | **L4-lite safety floor** | code-level verify pass + answer/clarify/**escalate**, abstain-default with proven-atom override (symmetric) | **+3** and **converts the bulk of confident-wrongs to safe-escalate** — the biggest safety win, cheap | 1–2 d |
| **C3** | **Wire the compute hatch** | register `query_database` in the trunk + regenerate the schema card from `information_schema` (both ~pre-built) | **+2** (V008 V014) | 1–2 d |
| **C4** | **`message_party_fact` + relationship/party-role capabilities** | the ACL-safe bridge once; distinct-message rollup replaces `total_mentions`; address-level vendor/client | **+6–8** (V018 V026 V031 V036 V042 V043) | 3–4 d |
| **C5** | **Entity dossier + Answer-IR renderer** | role-from-signature + the typed-claim renderer with mandatory claim classes | **+5** (V001 V002 V011 V019 V021) | 4–5 d |
| **C6** | **Tail** | document-canon by business-signal (cheap); thread/responsiveness/arc *only if they measure out* | **+1** clean (V010); rest → partial/escalate | 3–5 d |

**Week 1 = C1 + C2.** This ships the safety floor (confident-wrong → near-zero) *and* the cheapest
correctness wins *before any projection is built*. That is the highest-trust-per-day work on the board,
and it's mostly bug-fixes + a verification pass.

**Already built, sitting inert (de-risks the plan):** `sql_guard.py` (production-grade SELECT guard),
`query_database` (the delegated-SQL lane), and the `counterparty_summary` view (0020/0021, already
`security_invoker` — just domain-keyed and using the wrong metric). The revert of MUT11b (the dossier
tool) was a **grader artifact, not projection poison** — so the dossier direction is not tainted.

---

## 6. Guardrails and what stays escalated

**Anti-bias (must fix, it's currently violated).** Remove the hardcoded BG glossary from the tool
description; derive expansion from a **question-blind, versioned** general source (translation call,
corpus co-occurrence, or embedding neighbors). Re-measure on the **holdout** split — if dev≫holdout
persists after this, we're still overfit.

**ACL (foundational).** Live `security_invoker` views only; per-message facts carry the full visibility
triple; the RLS policy joins `email_id` to `acl_grant` with `revoked_at IS NULL` at query time (so
revocation is instant); mandatory cross-tenant **and** cross-visibility-plane negative tests
(`testing.md` non-negotiable). Never distinguish "does not exist" from "exists but you can't see it" —
both render as "insufficient visible evidence."

**Projection poison.** Versioning + watermark on every claim; shadow-recompute vs canonical; metamorphic
tests; the definition of every fuzzy metric surfaced in the claim. A confidently-wrong projection is
worse than a flaky reader because it fails on *every* question at once.

**The permanent escalate bucket (do not chase these to zero):** the ~3 genuinely judgment-bound questions
(stakes, importance); conflicting/temporally-changing vendor-client roles; ambiguous identity merges;
missing/OCR-failed documents; conflicting document versions without authoritative metadata; novel
multi-hop requests outside the capability grammar; any verifier or N-best disagreement.

**Measurement (the real decision instrument).** Freeze the 43 as a regression suite; author the next gold
set **blind and sealed**; report the **selective-accuracy curve** — % answered / % correct-when-answered
/ unsupported-claim rate / % escalated / cost — per deployment path. That curve, not a single score, is
how we decide production-readiness.

---

## Appendix A — the 43-question gate table
The full per-question diagnosis (verdict, evidence-in-context cut, failure class, fix bucket, plain-
language why) lives in `Benchmarks/_ask_loop/` autopsy artifacts and is summarized in
`EXP-002_ask-tools-loop-diary.md`. Headline distribution: precomputed-projection 17 (several *qualified*
— need company-merge or signature-NLP, not pure SQL), already-pass 10, query-expansion 7, decomposition 3,
verification 3, compute-hatch 2, answer-IR 1.

## Appendix B — provenance
Diagnosis triangulated across: the ask-loop code + cached transcripts; the EXP-001 multi-arm forensics
(Gemma/Opus/SQL-specialist on identical tools + judge SQL); Claude-4.8 advisor (×2); GPT-5.5/Codex (×2);
and a 5-agent adversarial panel (maximalist + surgical architects, red-team, ACL verifier, coverage
verifier) whose ACL-isolation and projection-coverage claims were re-verified as live SQL. Corpus/ceiling
figures from the 5,893-email ask-loop corpus are kept distinct from the EXP-001 lab's 5,982-email
refreshed corpus; the 79–84% strong-reader ceiling is a cross-world transfer, not a same-corpus
measurement.

---

## Addendum — status as of 2026-09-06

*Added 2026-09-06. Nothing above this line was changed: the diagnosis and the design of 2026-07-06 stand as
written. This addendum records only what has since been built, and which numbers above must no longer be
quoted. Every code claim was re-verified against the working tree on branch `ask-tools-loop`, HEAD
`1feb172`; the per-finding detail with anchors is in `ASK-02-findings-report.md`.*

**What landed (the L0/C1 bug-fix layer, ~all of it uncommitted).** F6 (Office-format MIME filter), F2
("between A and B" via `participants_all` party groups), F3 (no forced date window + `date_span` /
`span_boundary_emails` anchors), F8 (structural payload trimming that always emits valid JSON), F9
(`counterparty_summary` volume columns as `count(DISTINCT message_id)`, migration `0022`, **applied** on the
dev DB) and F13 (the hardcoded Bulgarian glossary deleted — 0 grep hits across `backend/app/`). F1's compute
path shipped not as a ranking tool but as the generated-SQL hatch (`sql_tool.py` `query_database` +
`sql_guard.py` + `sql_execution.py`) and the direct-SQL arm `sql_pipeline.py`.

**What did NOT land the way §1 asks for it.**
- **§1 reason 2 (Bulgarian blindness) is mitigated by a hint, not closed.** The zero-results trigger is
  gone and `search_emails` now always emits a `language_coverage` note when every term is single-script
  (`backend/app/ask/tools/email_search.py:230-243`) — but the **deterministic, always-on, question-blind
  expansion this document specifies does not exist**. The reader still has to author the translated and
  transliterated variants itself.
- **§1 reasons 3 and 4 are fixed** (the docx filter and the AND-participant filter respectively).
- Reasons 1 and 5 were not re-assessed in this pass; treat them as open until measured.

**The coverage numbers in §0 are superseded — do not quote the old band.**
`ASK-02-overnight-analysis-2026-07-07.md` §1 and §4.3 establish that the honest, DB-verified,
single-roll figure is **27/43 (62.8%)** — the *low* end of this document's "~30–34/43" projection, and
dev-selected at that. **33/43 (76.7%) is a per-question best-of-N union, reproducible in no single roll:
do not quote it.** The N=3 ensembling that would legitimately convert variance was never run. Everything
in §0's "Projected-contingent (C4–C5)" band stays a projection.

**Unresolved: the declared reader arm does not match the code.** This document's header declares
`google/gemma-4-31B-it`. As of 2026-09-06 the checked-in default is `ask_reader_model = "Qwen/Qwen3.5-9B"`
(`backend/app/core/config.py:137`, identical in the working tree and at HEAD `1feb172`); the adapter reads
it once (`backend/app/ask/adapters/together_chat.py:48`) and forbids a per-call override (`:76`), and no
`ASK_READER_MODEL` override exists anywhere in the repo. So the Gemma arm was never made the default.
This addendum records the discrepancy; **which arm is the arm is a founder call and is left open here.**

**Committed status.** Except migration `0022` (applied to the dev DB but intent-to-add with an empty blob
in git), none of the code named above exists in any git object, and branch `ask-tools-loop` has never been
pushed — see `docs/audits/2026-09-06_built-vs-docs-map.md` §4.
