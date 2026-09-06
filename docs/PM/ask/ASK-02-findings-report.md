# Ask Layer — Diagnostic Findings Report

> **Status as of 2026-09-06:** this stays the 2026-07-06 evidence register — no finding's evidence or
> wording below has been changed. What was added is a **Disposition (2026-09-06)** line under the findings
> that have since been acted on (F1, F2, F3, F6, F7, F8, F9, F13), and a current code anchor beside the two
> citations that pointed into `shared_core.py` before it was split (it is now a 41-line assembly module).
> Each disposition was re-verified against the working tree on branch `ask-tools-loop`, HEAD `1feb172`.
> **Caveat that applies to every "closed" below:** except migration 0022, none of the closing code is
> committed — the modules are untracked or intent-to-add with an empty blob, and the branch has never been
> pushed (`docs/audits/2026-09-06_built-vs-docs-map.md` §4). F4, F5, F10, F11, F12, F14, F15, F16 and F17
> carry no disposition line — they were not re-assessed in this pass, and this banner makes no claim about
> them in either direction.

**Question investigated:** Why can the small reader model not answer 100% of the benchmark questions when
the answers are provably present in the corpus — and what must change to reach ~100%?

**Date:** 2026-07-06 · **System under test:** the Ask layer (`backend/app/ask/`) — `google/gemma-4-31B-it`
answering over 5,893 ingested emails (8,454 attachments, 839 persons, bilingual BG/EN) through a bounded
8-turn tool loop with 6 SQL-backed tools. · **Companion design doc:** `ASK-02-small-model-to-100-safe.md`
(architecture + action plan). This report is the **evidence register**; that doc is the **fix**.

**Method.** Forensic per-question autopsy of all 43 benchmark questions, fusing the ask-loop's own cached
Gemma transcripts with the EXP-001 lab's multi-arm forensics (the *same* questions answered by Gemma,
Opus, and a SQL specialist on *identical* tools, plus the judge's verifying SQL). Every gold and every
finding below was **verified against the running database** or a real run transcript. A 5-agent
adversarial panel (3 architects + 2 verifiers) then designed and attacked the fix; the two
safety-critical claims (tenant isolation, projection coverage) were **re-run as live SQL**. Independent
second opinions: Claude 4.8 advisor (×2) and GPT-5.5/Codex (×2).

**Evidence legend:** ✅ VERIFIED = reproduced by the author on the live DB / in a real transcript.

---

## Executive summary

- **The data is present; the reader can't process it.** On the *identical* 6 tools, Opus scores **36/43**
  and Gemma scores **10/43** (5 more partial). The gap is reader competence at in-context reasoning, not
  data or retrieval. ✅
- **It is not a bandwidth problem.** Giving the small model 4× more context/results produced **zero** new
  correct answers. The cure is to pre-compute the answer, not to show more text. ✅
- **It is a *gathering* problem, not a *thinking* problem.** Of 33 non-passes, the needed evidence was
  **never gathered (17) or only partially gathered (12)** — the tools couldn't express the operation. Only
  **4** had the evidence and mis-composed it. ✅
- **But every failure is a *confident* wrong answer** — the single biggest enterprise-trust risk (F10).
- **100% autonomous is impossible** (Opus caps at 84%); the correct, reachable target is **100% *safe
  disposition*** — answered-correctly or honestly-escalated, near-zero confident-wrong.
- **Two of the "failures" are outright tool bugs** (F6, F7) with trivial fixes.
- **A tenant-isolation constraint governs the whole fix** (F12, Critical, reproduced): projections must
  never materialize aggregates.

Findings by severity: **Critical 3 · High 8 · Medium 4 · Low 2.**

---

## A. Capability-gap findings — the substrate wall (why evidence is never gathered)

### F1 — No aggregation / ranking / enumeration primitive · **High** · ✅
The tools can search and count-with-a-filter, but cannot rank or enumerate across the corpus. "Which
clients have the most email threads?" needs grouping thousands of rows; the model eyeballs a few domains
it stumbled on and misses every leader.
- **Evidence:** distinct-message counts — Ocenki 466, GBS group ~488 (gbs-bg 312 + gbs-is 120 + …), APIS
  179, Data+ 114 — all rank **above** every domain Gemma named (Breeze 58, Polar Moda 46). ✅
- **Impact:** the single largest failure family (~9 questions: rankings, "top-N", enumerations, "all X").
- **Fix:** live `security_invoker` relationship/thread rollups reached via the query-plan hatch.
- **Disposition (2026-09-06): PARTIALLY CLOSED — a compute path shipped, no ranking tool did.** No
  aggregation primitive was added to the six-tool set (deliberate; ASK-01 §4 tool-count fragility). What
  exists instead is the generated-SQL hatch: `query_database` (`backend/app/ask/tools/sql_tool.py:144`),
  validated by `sql_guard.py`, executed on the reader plane by `sql_execution.py` with row-id provenance
  from `sql_provenance.py`, plus the direct-SQL arm `backend/app/ask/services/sql_pipeline.py`
  (`run_eval --arm xiyan-routed`). Whether it closes this failure family is a measurement question this
  entry does not answer.

### F2 — Tools cannot express "between A and B" (participant filter is OR, not AND) · **High** · ✅
- **Evidence:** "emails between Yani and Mihail about Vetera" — the true pair-count is **exactly 72**
  (matches gold); the tool's `participants=[Yani,Mihail]` returns **~5,879** (essentially the whole
  corpus). ✅
- **Impact:** every "correspondence between two parties" question is uncomputable; the model falls back to
  fetching emails one-by-one and times out.
- **Fix:** a typed `participant_groups` + `all_groups` slot in the query-plan hatch.
- **Disposition (2026-09-06): CLOSED in the tools themselves.** `participants_all` takes up to 4 party
  groups; each group is an OR over that party's aliases and the groups AND together —
  `_party_group_clause` at `backend/app/ask/tools/email_filters.py:33-45` (static conjuncts, so the
  statement text never varies) and `_participants_all_groups` at `:112`, which **raises** on oversized or
  empty input rather than silently widening the match. Exposed on both `search_emails` and `count_emails`.

### F3 — No "oldest / earliest" path (search refuses without a date window) · **High** · ✅
- **Evidence:** "oldest email thread" — the real oldest is 2023-06-12; `search_emails` won't run without a
  `date_from`, so the model blind-guesses windows from 1900 forward, every one empty, and burns all 8
  turns before reaching 2023. ✅
- **Impact:** all first/earliest questions; wasted turn budget.
- **Fix:** an `order=sent_at_asc` path + completeness envelope on every list tool.
- **Disposition (2026-09-06): CLOSED, by a different mechanism than proposed.** There is still no
  `order=sent_at_asc` parameter; instead `search_emails` no longer requires a date window at all — it
  accepts any one of `queries` / `participants` / `participants_all` / `date_from`
  (`backend/app/ask/tools/email_search.py:51-54`) — and every envelope now carries `date_span`, the true
  earliest and latest matching message with citable ids (`:99-112`), plus `span_boundary_emails`, the 3
  earliest + 3 latest (`:142-172`). The tool description points the reader at `date_span` for
  earliest/latest facts (`:292-293, :310`).

### F4 — No thread concept exists in the data model · **Medium** · ✅
- **Evidence:** the schema has no thread column; "how many threads" is answered with the raw message count
  (5,893) instead of ~2,962. ✅
- **Impact:** thread-count and thread-ranking questions.
- **Fix:** a **versioned** thread key from `in_reply_to`/`references` forest only (never subject-merge),
  always returned with its method + uncertainty (see F15 — this metric is partly definitional).

### F5 — No entity / company identity resolution · **High** · ✅
The identity graph under-merges: one company's domains and one person's addresses are treated as separate.
- **Evidence:** GBS spans gbs-bg + gbs-is + gbs-sofia (must merge to ~488 / 36 people to rank #1); person
  "Albena" has 2 addresses (dataplus + icloud) and her 20 documents split to 7–8 unless merged; even the
  "72" pair-count shifts to 80 depending on which addresses count as "Yani". ✅
- **Impact:** silently corrupts *every* ranking/enumeration/dossier that crosses a domain or address
  boundary — and it is the hidden dependency that makes several "projection wins" contingent (see F17).
- **Fix:** an address-set / domain-cluster resolver, **versioned + shadow-recomputed + metamorphic-tested**
  before any dossier keys on it.

---

## B. Tool-defect findings — plain bugs (a correct question returns the wrong answer)

### F6 — Attachment type filter matches nothing for Office formats · **High** · ✅
- **Evidence:** `search_attachments(content_type="docx")` → SQL `ILIKE '%docx%'` → **0 rows**, while there
  are **811** real `.docx` (MIME `application/vnd…wordprocessingml.document`, which does not contain the
  string "docx"). Same for xlsx/pptx. `shared_core.py:349` *(2026-09-06: that executor now lives in
  `backend/app/ask/tools/attachment_tools.py`)*. ✅
- **Impact:** "do we have Word/Excel docs about X" returns "no" for the most common document types.
- **Disposition (2026-09-06): CLOSED as proposed.** `_MIME_FAMILIES` maps docx/doc/word, xlsx/xls/excel,
  pptx/ppt, pdf, image and zip onto their MIME-family patterns
  (`backend/app/ask/tools/attachment_tools.py:38-50`); the filter became
  `content_type ILIKE ANY(:content_type_likes)` **OR** a filename-extension fallback `%.<type>`
  (`:82-83`; family lookup at `:69`, parameters bound at `:95-98`, and the extension stripped of every
  LIKE metacharacter at `:63-68`).
- **Fix:** a format→MIME-family map (≈1 line of data).

### F7 — Bilingual search is ~90% blind, and the fallback is broken by design · **High** · ✅
- **Evidence:** `invoice` matches **40** emails; `фактура` matches **395** (union 416). The model searches
  English, finds 40, and stops — because the auto-translate fallback only fires when a search returns
  **exactly zero**, so a partial hit masks the truth. ✅
- **Impact:** every money/contract/expense question in a Bulgarian-dominant archive.
- **Fix:** deterministic, always-on, **question-blind** bilingual + morphological expansion (see F13 — the
  current expansion is a hardcoded, benchmark-tuned glossary, which is a separate problem).
- **Disposition (2026-09-06): NOT CLOSED as specified — what shipped is advisory, not expansion.** The
  zero-results trigger is gone: `search_emails` now emits an always-on, question-blind
  `language_coverage` note whenever every supplied term is single-script, naming the script the counts do
  **not** cover (`backend/app/ask/tools/email_search.py:230-243`), plus a translate/transliterate retry
  hint on an empty result (`:224-229`). But **no deterministic term expansion exists** — the reader must
  still author the translated and transliterated variants itself into `queries[]`, so a partial
  single-language hit can still be reported as the total if the model ignores the note. Independently
  reconfirmed in `ASK-02-overnight-analysis-2026-07-07.md:53-55`.

### F8 — Payload cap truncates mid-JSON and drops trailing fields · **Medium** · ✅
- **Evidence:** the 6,000-char observation cap (`agent_runner.py:208`) slices the JSON string arbitrarily,
  producing malformed JSON; `get_email` emits the body *before* recipients/attachments, so a long body
  truncates away the recipient and attachment lists the model needs. ✅
- **Impact:** intermittent evidence loss on long messages; the model parses broken payloads.
- **Fix:** structured truncation with `{truncated, total_rows, next_cursor}`; never string-slice.
- **Disposition (2026-09-06): CLOSED.** `_fit_payload` (`backend/app/ask/services/agent_runner.py:46-151`)
  replaced the arbitrary slice: it trims structurally (bulky list fields first), preserves the control
  fields, records a `truncated` marker on every trim, and always hands the model **valid JSON** — including
  the degenerate case, where it emits a valid envelope saying the result could not be serialized instead of
  a broken string (`:127-152`). Cap is `_TOOL_RESULT_CHAR_CAP = 6000` at `:34`, called at `:337` and `:343`.
  The ordering half is fixed at the tool: `search_emails` emits its bulky `results` page **last**, so the
  budget can only ever cost snippet rows, never `total_matches`/`date_span`/`listing_complete`
  (`backend/app/ask/tools/email_search.py:88-92, 244-246`).

### F9 — The one existing rollup view uses a double-counting metric · **Medium** · ✅
- **Evidence:** `counterparty_summary.total_mentions` counts every contact edge, so a message on which a
  domain appears on multiple recipient lines is counted more than once; ranking by it puts APIS at 22.4/wk
  above the true leader GBS at 5.35/wk. ✅
- **Impact:** any ranking built on the shipped view is subtly wrong — part of why the reader never trusted
  it.
- **Fix:** `count_distinct(message_id)` / `count_distinct(thread_key)` in the replacement rollup.
- **Disposition (2026-09-06): CLOSED and APPLIED.** Migration `0022_counterparty_summary_v3` recreates the
  view with `inbound_count` / `outbound_count` / `total_mentions` as `count(DISTINCT message_id)`
  (`backend/app/db/migrations/versions/0022_counterparty_summary_v3.py:51-53`), keeping the column names
  and the citable `first/last_message_id` unchanged, still
  `WITH (security_invoker = true)`. It is the **applied alembic head** on the dev DB — `alembic_version` =
  `0022_counterparty_summary_v3`, measured 2026-09-06 (`docs/audits/2026-09-06_built-vs-docs-map.md` §3).
  The thread-key half is not built (no thread concept exists — F4). Caveat: the migration file itself is
  intent-to-add with an empty blob, so the applied database is stamped at a revision that exists in no git
  object.

---

## C. Safety findings — the enterprise-trust risk

### F10 — No abstention: every failure is a *confident* wrong answer · **Critical**
- **Evidence:** across all failing questions the model states fabrications as fact — "5,893 threads",
  "GBS has 6 people", "nothing went wrong with Kaufland"; in a sibling run it **invented a "relationship
  expansion" out of a thread that literally contained a cancellation.** ✅ (transcripts)
- **Impact:** the product-killer. A confidently-wrong answer is worse than a miss for a trust product.
- **Fix:** a deterministic verification gate + `answer/clarify/escalate` disposition; unsupported-claim
  rate becomes the headline metric.

### F11 — Abstention is broken in *both* directions (it also refuses valid answers) · **High** · ✅
The model also *refuses deterministic leaps it should make*.
- **Evidence:** "Kaufland emails in Trash?" — it correctly noticed the archive stores no folders, then
  said "I cannot tell" instead of the correct "therefore zero." "Time from first contact to signed deal?"
  — it wrote both anchor dates in its own answer, then declared the duration "cannot be calculated." ✅
- **Impact:** loses answers that are provably derivable; over-escalation.
- **Fix:** a **symmetric** verifier that forces the answer when it is deterministically derivable and
  escalates only when it is not.

### F12 — Materialized aggregates leak across the tenant-visibility plane · **Critical** · ✅ (reproduced)
The governing constraint for the entire projection design, verified by the author in a rolled-back
transaction.
- **Evidence:** a principal granted **100 of 5,893** messages, reading a **materialized** counterparty
  rollup, saw **383 domains / 18,728 mentions — the entire org's graph**, including domains from the
  5,793 messages they cannot read. The **live `security_invoker` view** gave the same principal only
  **61 domains / 309 mentions — exactly their visible slice.** ✅
- **Impact:** a materialized projection is a cross-tenant / cross-visibility data-leak — a contract breach
  under One AI's isolation rules.
- **Fix (non-negotiable):** never materialize a tenant aggregate. Materialize **per-message facts**
  (carrying `email_id` + the full PF-01 visibility triple + the identical RLS policy) and aggregate at
  query time through `security_invoker` views. A whole-corpus GROUP BY runs in **29 ms** ✅, so
  materialization buys nothing.

---

## D. Governance & measurement findings

### F13 — The shipped tool hardcodes the benchmark's Bulgarian glossary (anti-bias violation) · **High** · ✅
- **Evidence:** `shared_core.py:464-466` *(2026-09-06: that code is gone — see the disposition below;
  `shared_core.py` is now a 41-line assembly module)* lists `offer (оферта, предложение), contract (договор),
  invoice (фактура) … expenses/business trip (разходи, командировъчни), deadline (краен срок)` — the exact
  idioms the golds reward. ✅
- **Impact:** violates the founder-set "no corpus/benchmark literals in model-visible surfaces" rule, and
  **plausibly explains dev (27%) ≫ holdout (13%)** — the fix is soft-tuned to seen questions.
- **Fix:** derive expansion from a question-blind general source (translation / embeddings / corpus
  co-occurrence); re-measure on holdout to prove it generalized.
- **Disposition (2026-09-06): CLOSED by removal, not by replacement.** The glossary is gone: grepping
  `оферта|предложение|договор|фактура|разходи|командировъчни|краен срок` across all of `backend/app/`
  returns **0 hits** (2026-09-06), so no benchmark literal remains in a model-visible surface. Nothing
  question-blind replaced it, however — the expansion the Fix line asks for was not built, which is why F7
  above is only advisory.

### F14 — The 79–84% strong-reader ceiling is a cross-corpus transfer, not a same-corpus measurement · **Medium** · ✅
- **Evidence:** the Opus 34/43 (79%) and 36/43 numbers come from the EXP-001 lab's **5,982-email**
  refreshed corpus + shim, not the ask-loop's **5,893-email** corpus. ✅
- **Impact:** use it as directional evidence of the reader gap, not as a precise same-corpus target; do not
  average across the two worlds.
- **Fix:** re-run the strong-reader arm on the ask-loop corpus before quoting a firm ceiling.

### F15 — Part of the ceiling gap is *definitional*, not competence · **Low** · ✅
- **Evidence:** "how many threads?" has no single right answer — raw messages 5,893, reply-roots 2,229,
  distinct-normalized-subjects 2,104; the gold wants ~2,962 and no simple method even reaches its floor
  (2,812). ✅
- **Impact:** some "misses" are the model failing an under-specified question; these belong in
  clarify/define, not model-failure.
- **Fix:** surface the metric definition in the answer; treat as `clarify` when the definition is
  contested.

### F16 — Root cause confirmed: reader competence, and caps don't move it · **High** · ✅
- **Evidence:** Gemma 10/43 vs Opus 36/43 on identical tools; the EXP-001 "bigcaps" run (4× caps) yielded
  **0 new Gemma passes, 0 regressions** — Gemma still enumerated 13 of 72 and still misattributed. ✅
- **Impact:** settles the strategy — do not invest in bigger context/turns; invest in pre-compute + a
  reader-authored compute path (the small model *can* compute via SQL the DB runs, just not in its head).

### F17 — The "17 projection wins" is optimistic; several are *contingent* · **Medium** · ✅ (live SQL)
- **Evidence:** the coverage verifier ran the actual SQL. Clean one-shot wins: **V004** (oldest), **V011**
  (docs-by-person after address-merge). But **V026/V018/V031** are correct only if companies are merged
  across domains (F5); **V001/V021-role** need signature-NLP, not SQL; **V010** document-canon by pure
  ubiquity ranks the founder's personal deck (44 copies) **above** the real company deck (33); vendor
  labeling (**V042/V043/V036**) needs the riskiest projection (party-role). ✅
- **Impact:** the projection layer's payoff depends on entity-resolution + signature-NLP + party-role
  quality — each a "projection-poison" risk (a wrong projection fails on *every* instance at once).
- **Fix:** treat C4/C5 gains as *to-be-measured*, not booked; build each contestable projection with
  versioning + shadow-recompute + metamorphic tests + the metric definition surfaced in the claim.

---

## Findings → fix mapping (see `ASK-02-small-model-to-100-safe.md` for the full plan)

| Fix stage | Closes | Effort |
|---|---|---|
| **Week 1 — tool bugs + safety floor** | F6, F7, F3, F8, **F10, F11** | small (bug-fixes + a verify/abstain pass) |
| **Compute hatch** (typed query-plan) | F2 | ~pre-built (`sql_guard.py`, `query_database`) |
| **Per-message bridge + live rollups** | F1, F9, F4, F12 | medium |
| **Dossier + typed-claim renderer** | F5-dependent, F17 | medium |
| **Governance (parallel)** | F13, F14, F15 | small–ongoing |

**Bottom line:** the small model plateaus because the tools force it to compute in its head and let it
guess when it can't. Move the computation into the database (tenant-safely, per F12), let it render typed
facts it cannot alter, and make "escalate" a first-class answer. Autonomous-correct rises from **10/43 to
~30–34/43** (the strong-reader ceiling), and **safe disposition reaches ~93–100%** — most of it captured
in week 1 by fixing bugs and adding a safety pass.

---

## Appendix — 43-question gate table
Full per-question diagnosis (verdict, evidence-in-context cut, failure class, fix bucket, plain-language
why) is persisted in the autopsy artifacts under `Benchmarks/_ask_loop/`. Distribution of the single
highest-leverage fix per question: precomputed-projection 17 (several *contingent*, per F17), already-pass
10, query-expansion 7, decomposition 3, verification 3, compute-hatch 2, answer-IR 1.
