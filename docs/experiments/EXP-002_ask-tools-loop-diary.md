# EXP-002 — ASK-Tools Optimization Loop: Full Experiment Diary

**Role:** Complete chronological record of every experiment in the ask-tools optimization campaign
(2026-07-04 → ongoing) — what we tried, exactly what happened, and the lesson — so no approach is
ever unknowingly repeated.
**Used by:** anyone continuing the Ask-layer work; future mutation campaigns; the CKPT verifiers.
**Depends on:** `Benchmarks/_ask_loop/ledger.md` (the authoritative per-run journal with exact
configs/hashes), `Benchmarks/_ask_loop/runs/` (all artifacts), `backend/app/ask/` (the system under
test).
**Key invariants:** every score here is an N=3 majority-vote measurement unless marked otherwise;
nothing in this file is from memory — it mirrors the ledger; if this file and the ledger disagree,
the ledger wins.

---

## 0. The setup (read once)

- **System under test:** a small reader model answering business questions over an ingested email
  archive (5,893 emails, bilingual BG/EN, PostgreSQL + RLS) through SQL-backed tools in
  `backend/app/ask/tools/`, driven by `services/agent_runner.py` (8-turn cap).
  *(Path corrected 2026-09-06: the executors moved out of `shared_core.py` during the campaign this
  diary records. The six-tool core now lives in `person_tool.py`, `email_search.py`, `email_read.py`
  and `attachment_tools.py`; `email_filters.py` + `tool_helpers.py` are shared helpers; the SQL lane
  is `sql_tool.py` / `sql_execution.py` / `sql_guard.py` / `sql_provenance.py`; `shared_core.py` is
  now the assembly module that builds the six-tool baseline registry. Only `shared_core.py`,
  `registry.py`, `sql_guard.py` and `sql_tool.py` are tracked in git — the rest are working-tree
  files as of 2026-09-06.)*
- **Benchmarks:**
  - `questions_v1.json` — 59 questions (33 dev / 26 holdout), gold authored against the DB.
    RETIRED 2026-07-06 (5 questions proven only partially answerable → ceiling dilution).
  - `questions_v2.json` — 43 founder-cleaned questions, ALL verified fully-email-answerable
    (28 dev / 15 holdout), email-scoped golds + per-question winning `method` notes.
    Current pin: **v2.1, sha `6eaf1db9fdbf423e`**.
- **Measurement protocol (hard-won, see lessons #3, #5, #6):** every candidate runs **N=3 reps**,
  question passes at **majority (≥2/3)**; two-tier grading — deterministic typed golds
  (count/date/entity/list/no_data) + Opus critic doing claim-by-claim entailment with
  tool-payload cross-checks; **pre-registered gates per rung** (rung1 ~12 qids → rung2 +12 →
  full-dev; accept only at full-dev with ≥ +3 questions over parent); independent **Opus verifier
  audit at every accept/checkpoint**.
- **Anti-bias constraint (founder-set):** tools must be universal — no benchmark/corpus literals in
  any model-visible surface (verifier-audited every checkpoint).
- **Reader arms so far:** `Qwen/Qwen3.5-9B` (Together API) → `google/gemma-4-31B-it` (Together API).
  Model identity is frozen per campaign arm; params are mutable.

**Score arc:** naive 12.1% → qwen+MUT5 24.2% → Gemma trunk 27.3% → Gemma+stack 33.3% (v1) /
**32.1% honest claim-level on v2** (where 100% is actually reachable). Zero fabricated citations in
~1,500+ graded episodes.

---

## 1. Era 1 — qwen3.5-9B on questions_v1 (2026-07-04 → 07-05)

### Baseline
Naive agent + 5 tools (search_emails, count_emails, find_person, get_email, search_attachments).
**Result: 7/33 dev (21.2%) initially graded; honest re-grade after grader hardening → 12.1%.**
Failure histogram that drove everything after: bilingual-search-miss→false-no-data (~7q),
enumeration-recall-collapse (6q), shallow-synthesis (4q), missing-citations (3q), derived-number
(2q), window-discipline, ambiguity.

### MUT1 — bilingual multi-term search (`queries[]`)
Search accepts up to 5 OR'd variants; description asks for translations/transliterations.
**ESCALATED rung 1, died full-dev (+2 < +3). Lesson: right mechanism, needed the citation contract
with it.**

### MUT2 — result envelope (total_matches + per_term_matches + subject-first ranking)
**Rejected initially due to a CACHE POISONING BUG** (see lesson #2), clean rerun ESCALATED;
component kept as substrate for MUT5.

### MUT3 / MUT4 — citation-hardened prompt iterations
Prompt requires `[id: <uuid>]` on every factual claim; answers without citations invalid.
Intermediate rungs mixed; became part of MUT5.

### ★ MUT5 — the composite that ACCEPTED (commit 5463aef)
queries[] + envelope + subject-first ranking + hard citation contract.
**Result: 12.1% → 24.2% (+4 questions, biggest single jump of the campaign). Side effect that
never regressed since: fabricated citations dropped to ZERO and stayed there.**
Verifier audit: CLEAN.

### MUT6–MUT10 — model-params + prompt-procedure family (ALL REJECTED)
Tried: temperature/max_tokens variants, more agent turns, "check both languages" prompt lines,
self-verification instructions, plan-first procedures.
**Result: none survived a rung. Two lessons: (a) params don't fix judgment at 9B; (b) prompt
procedures interfere with each other when stacked (proven properly later, MUT17).**

### MUT11/MUT11b — counterparty_summary SQL view + tool
Materialized per-counterparty rollups (domain, first/last contact, counts) + a tool over it.
**MUT11b was ACCEPTED (+3, commit d564df0) then RESCINDED (commit e4a4535): the verifier's
re-grade proved the delta was +1, inflated by two grader artifacts (list-index/timestamp numbers
counted as counts; MDY dates unparsed).** The view itself survives in the DB (0020/0021) and is
still believed useful — the *reader never routes to it* (open item, "counterparty rollup tool").
**Lesson #3: never trust a delta before grader conformance; every accept needs verifier re-grade.**

### MUT12 — intent router + per-class toolkits (the "router" idea)
classify_question → 6 intent classes → class-specific tool subsets + prompt addenda.
Standalone router accuracy after parse fix: 70%.
**Result: routed arm 18.2% vs 24.2% generalist — LOST 2 questions. Routing errors cost more than
specialization gained at 9B. PARKED with rematch conditions (better classifier model or
tool-count >8).** Do NOT re-try as-is on a small model.

### MUT13/MUT14 — degenerate-answer guard + empty-answer nudge
Scaffold hardening for the reasoning-channel problem (Together/qwen "reasoning" eats completion
tokens → empty answers). max_tokens 4096 + one nudge retry ACCEPTED as harness fix (not scored as
mutation). MUT14's stricter guard diff preserved in `mutations/`, not in tree.

### XiYanSQL arm (user-directed)
`XGenerationLab/XiYanSQL-QwenCoder-7B-2504` (Ollama, Q4_K_M) as a text-to-SQL tool behind
PF-FBP-8-style SQL guard (SELECT-only, single statement, comment-strip, forced LIMIT, GUC deny).
Two shapes tested: (a) SQL tool exposed to reader, (b) xiyan-routed arm.
**Result: the SQL model wrote excellent SQL when reached; the 9B reader delegated to it wrongly or
not at all. Both shapes REJECTED. Infrastructure KEPT (guard + pipeline are production-grade).
Lesson: the bottleneck was the delegator, not the SQL. Rematch condition: stronger reader.**

### Era-1 close: BLOCKED report
After 14 mutations across 3 approach families, the 9B judgment ceiling was declared with evidence.
**Founder decision: switch reader to google/gemma-4-31B-it (params:new-arm rule).**

---

## 2. Era 2 — Gemma 4 31B on questions_v1 (2026-07-05 → 07-06)

### MUT15 — Gemma calibration (the +9-points-for-free lesson)
Raw Gemma on the qwen harness: **18.2%** — WORSE than qwen's 24.2%.
Fixes: `max_tokens` 8192 (bigger reasoning channel), `ASK_READER_TIMEOUT_SECONDS=300`,
end-of-prompt citation reminder suffix.
**Result: 27.3% trunk (9/33: Q002 Q003 Q004 Q007 Q008 Q018 Q043 Q046 Q059). Lesson #7: NEVER
judge a model arm before calibrating mechanics; an untuned harness under-measures by ~9 points.**

### MUT16/MUT18/MUT20 — authored-counts procedure (prompt channel)
"Compare activity by messages AUTHORED in the window, not involvement/CC."
**Q041 flipped every time the procedure was followed (4 separate configs across the campaign) —
but the procedure fired stochastically rep-to-rep (prompt channel). None of the three candidates
cleared cumulative gates (+1 each).**

### MUT17 — bundling two prompt procedures (NEGATIVE RESULT, important)
Authored-counts + timeline procedure in ONE suffix.
**Q009 collapsed (present in both singles), bundle rejected. Lesson #4 (channel-split law v1):
multiple procedures in one prompt channel interfere; one procedure per channel.**

### MUT19 — attachment text tools
`text_snippet` in search_attachments + `get_attachment` executor (5,000-char cap).
**Rejected at rung (+1) but mechanism proven: Q034's pricing figures became retrievable for the
first time. Kept as substrate.**

### Oracle wave (user-directed; 6 Opus agents, unrestricted DB access)
Question: "is the data the problem?" **Answer: NO — 18/23 hard questions ANSWERABLE-GOLD-MATCH,
5 PARTIAL (gold already correctly projected), 1 literal unanswerable, 0 gold errors.**
Byproduct: the 7-primitive tool roadmap (attachment text first-class; complete-address person
expansion; signature-block titles; now=max(sent_at); document-content-over-metadata;
presupposed-entity honesty; inbound-burst reactivation proxy). This roadmap drove MUT19–MUT24
and was independently confirmed by the founder's benchmark `method` fields later.

### MUT21/MUT21b — find_person v2 (signature_block + unlinked_addresses)
Signature from the person's most recent NON-REPLY authored message (replies end in QUOTED history
— live-caught trap); scan for sender addresses not linked to any person row.
**MUT21 rejected (−1): title was in the payload, reader didn't surface it (retrieval-vs-synthesis
gap). MUT21b added a usage directive to the tool description → Radka's title started surfacing
(2/3). Rejected on guard flicker, mechanism banked. Boyan's title = data-side gap (his latest
non-reply is a casual note with no signature).**

### MUT22 — four-channel full stack (attachments + find_person v2 + doc-note + authored-counts)
**Rung 1: ESCALATED (6v5, Q041 flip, Q024 FIRST-EVER complete title+rail answer, Q035 GBS
concession found first time). Rung 2: REJECTED (cumulative 8v9). FABRICATION FLAG: one rep
invented identity merge "Tzenka = Stilyana" (два различни човека) — confirmed fabrication by
CKPT2 verifier; scoreboard-inert (failing answer) but the canonical example for the misattribution
failure class.**

### MUT23 — participants[] enumeration (multi-address/domain OR filter)
search/count accept participant LISTS; live-verified: Data+ address space = 187 emails (the
oracle's exact number), lilia's June-4 anchor = first result.
**Rejected (delta 0) BUT root-cause isolated: find_person('Data+') literal-matches NOTHING —
company names don't match address strings → enumeration starves.**

### MUT24 — name→domain resolution (matching_domains)
Alphanumeric normalization ('Data+'→'dataplus') + domain scan feeding participants[].
**Rejected (delta 0) BUT: Q005 PASSED for the first time in the whole campaign (rep2 reached both
timeline anchors 2025-03-16 and 2026-06-04); Q026 first det pass. All single-rep. Residual:
results are newest-first, readers render the recent slice and miss the timeline HEAD.**

### MUT24b — oldest-first timeline procedure (prompt channel)
**Rejected — and the prompt instruction actively FAILED: 0/3 reached the first-email anchor; the
rep that previously had it LOST it. Lesson: prompt-side ordering cannot fix tool-side ordering.
The +2 profile (Q024/Q041 flips, zero regressions) triggered the stack's full-dev acceptance run.**

### STACK full-dev acceptance run (33q ×3)
**Result: 11/33 = 33.3% vs trunk 9/33 = 27.3% → +2 < +3 → REJECTED as accept, but best v1 config
ever. Q038 (deep synthesis) passed 3/3 — first ever. Two checkpoint-grade discoveries by the
critic: (1) Q029-class GOLD DEFECT — attachment CRM.xlsx IS a client-status table with 9 'Frozen'
rows (the oracles missed it too; v1 holdout Q027/Q036/Q039 golds retroactively doubted); (2) Q048
cross-entity misattribution (real DataPlus numbers pinned on Breeze).**

---

## 3. Era 3 — questions_v2 + CKPT2 (2026-07-06 → ongoing)

### Benchmark migration (founder-delivered)
`benchmark-email-only.json` → `questions_v2.json`: 43 questions, all high-confidence fully
answerable, zero email-vs-truth conflicts, six NEW questions, per-question `method` fields
(the Opus winning strategies — they independently confirmed the oracle roadmap AND supplied 4 new
primitives: BG business vocabulary, free-mail vendor addressing, attachment-filename flags,
name-string fallback). Gold typed schemas authored by 6 Opus agents against the LIVE DB
(DB-truth discipline: corpus ends 2026-06-06; every filesystem-vs-DB divergence flagged;
V023 → no_data with SQL evidence).

### CKPT2 (verifier: PASS-WITH-FINDINGS; stack adopted as trunk-v2, commit 1feb172)
- v2 baselines: **STACK 10/28 = 35.7%, TRUNK 9/28 = 32.1%** — BUT the verifier proved the sole
  stack edge (V030) was a **gold-typing artifact** (entity-substring grading passed answers that
  attributed the property to the WRONG contact). V030 retyped → **honest result: STACK == TRUNK
  9/28 claim-level.** Stack adopted anyway: never-worse on both benches + real v1 gains + zero cost.
- **v2 HOLDOUT: 0/15** (dev–holdout gap fails the ≤5pp gate as measured; diagnosed as
  question-mix difficulty — holdout owns the hardest unseen entities; same three walls as dev).
- Fabrication docket CLOSED: the recurring "anachronistic Claude Code/Opus emails" flags =
  **REAL corpus content** (Yani's own AI-tooling mail, e.g. 2026-06-01 "Новости в Claude Code");
  Tzenka=Stilyana = confirmed fabrication; payload-aware critic caught everything correctly.
- Gate-interpretation of MUT24b adjudicated LEGITIMATE-BUT-LOOSE → new law: escalation judged
  against the CURRENT mutation's declared targets only.
- Hygiene: gold-entity comment scrubbed from code; org_id added to 2 SELECTs; conformance 16/16.

### V2-MUT0 — date_span anchors (tool)
Search envelope returns true earliest+latest matching messages.
**Rejected (delta 0). Mechanism partially landed: V021 now cites the exact real span. Blocking
claims were role-title/evolution — different wall. KEPT in tree.**

### V2-MUT1 — Bulgarian business vocabulary (prompt channel)
оферта/договор/фактура/подизпълнител/командировъчни/краен срок pairing instruction + attachment-
filename note.
**R1 ESCALATED (V041 flip 3/3 — unanimous). R2 REJECTED (cumulative +1): first-ever V019 pass and
Tridea-named V027s — all single-rep scatter. Attribution win: V041 did NOT re-flip in later
configs without this line → the flip belongs to the vocabulary.**

### V2-MUT3 — attachment paging (tool)  ← ran before MUT2 on fresh evidence
`offset`/`next_offset` on get_attachment; "totals sit near the END".
**Rejected (delta 0) BUT PERFECT 3/3 CONSISTENCY: every rep reached the Tridea contract's
19,817 EUR + 40/60 split past the old 5,000-char cap — first time ever. Residual: readers
summarized only the paged tail, dropped the party names.**

### ★ Lesson #1 — THE CHANNEL-CONSISTENCY LAW (the campaign's most valuable finding)
**Instructions in TOOL descriptions/payloads fire consistently across reps (3/3); instructions in
the prompt suffix fire stochastically (1/3–2/3).** Proven across: paging (3/3), parties directive
(3/3), date_span; vs BG-vocab/authored-counts/timeline procedures (scatter). Five candidates died
to prompt-channel scatter before this was named. **Nuance from MUT5-v2: for SEARCH-TERM generation
(what to type into queries) both channels scatter somewhat; for ANSWER-SHAPING behavior the tool
channel is near-deterministic. Corollary: express everything expressible as a tool change as a
tool change.**

### V2-MUT3b — "name the parties" directive (tool description)
**R1: V027 FULLY CONVERTED 3/3×3 (ТРИДЕА ООД + ЕТЕРА + 19,817 + 40/60 + Gemini carve-out, zero
fabrication) — the campaign's cleanest conversion chain: cap → paging → parties, all tool-channel.
Guard V032 dropped 1/3 → same-config probe ×3 → 2/3 PASS → ruled serving noise (V032 = semi-
fragile). ESCALATED on probe adjudication. R2: REJECTED (cumulative 9v9; V021 title now reliable
3/3 but claim-3 role-evolution synthesis still fails; V029 unchanged; V039 needs the vocab that
wasn't in this candidate).**

### V2-MUT5 — BG vocabulary moved INTO the search-tool description
Same measured content as V2-MUT1, better channel.
**R1 ESCALATED (8v7, V041 flip, zero regressions, banked V027 held 3/3×3; V039 progressed again —
Factis named 2/3, командировъчни sweep fires 3/3; remaining: Maria Kareva + Справка-noise
exclusion). R2 in flight at time of writing.**

### Codex cross-vendor consult (GPT-5.5, 2026-07-06)
Full reply: session scratchpad `codex_strategy_reply.md`; summary in ledger. Adopted:
completeness-typed enumeration design (total_count/returned_count/complete + server dedup +
stable order + never imply exhaustiveness), unified counterparty rollup tool, READER-ORACLE probe
(gold evidence direct → ≥90% ⇒ keep small model & restructure DB; material failure ⇒ test bigger
reader), person enrichment as immutable span-grounded observations (NOT canonical truth; delay
aggressive merging), best-of-N demoted to validator-triggered retry.

---

## 4. DO-NOT-REPEAT catalog (the whole point of this file)

1. **Router + per-class specialists on a ≤9B reader** — measured −6pp. Rematch only with a
   stronger classifier or >8 tools.
2. **Text-to-SQL specialist behind a small delegator** — the SQL was never the problem; the
   delegation was. Rematch only with a stronger reader.
3. **Model-params-only mutations** (temperature, turns, token budgets beyond calibration) — zero
   accepted deltas at two model sizes.
4. **Stacking multiple procedures into one prompt suffix** — MUT17's interference is real.
5. **Prompt-side fixes for tool-side properties** (ordering, truncation) — MUT24b failed exactly
   this way; fix the tool.
6. **Entity-substring golds for attribution questions** — V030's false edge; type them text/critic.
7. **Trusting any accept without verifier re-grade + conformance pin** — MUT11b false accept.
8. **Caching answers without hashing params AND tool/runner source code** — 3 poisoning incidents.
9. **Single-run verdicts on Together at temp 0** — 3/12 same-config verdict flips measured; N=3
   majority is the floor.
10. **"Recent window" questions against a stale corpus** — corpus ends 2026-06-06; honest no_data
    is the correct answer; don't chase it as a bug.
11. **Assuming client-status labels aren't email-derivable** — CRM.xlsx (attachment 80cca868)
    proved otherwise; check attachment TABLES before writing no_data golds.
12. **Grading enumeration recall against payloads capped at 10 results** — raise/handle the limit
    before blaming the reader (found 2026-07-06; enumeration-mode fix designed, not yet run).

## 5. Standing machinery (don't rebuild, reuse)

- Ladder: `scripts/ask_loop/run_eval.py` (config-hash incl. code_sha, --qids/--split, prompt-suffix
  lever, N-rep protocol) + `grade.py` (typed golds + critic worksheets w/ payload excerpts) +
  `conformance.py` (16 pinned grader cases — extend, never shrink).
- Agents: `critic-tier` (claim-level grading), `ask-tools-verifier` (isolated audits),
  gold-author pattern (6-way split, DB-truth discipline, spec files in `_ask_loop/`).
- DB snapshot for resets: `db_snapshots/post_backfill_2026-07-04.dump`.
- Codex CLI for cross-vendor opinions (headless `codex exec`, see memory note).

## 6. Where we are + the queue (as of 2026-07-06 ~16:00 UTC)

> **Note added 2026-09-06 — this section is a 2026-07-06 snapshot; the numbers ledger has moved
> past it.** Per this file's own key invariant (if the diary and the ledger disagree, the ledger
> wins), the state of record is `Benchmarks/_ask_loop/ledger.md` (outside the repo). What the
> ledger records after this snapshot was written: **V2-MUT5 REJECTED at R2** (cumulative 11 vs 10,
> +1 < +2), then the XiYan-Gemma arm rejected at R2 and V2-MUT6 / V2-MUT6b rejected at R1 —
> **V2 plateau counter 8/20**, not the 5/20
> and "MUT5 in flight" below; the singles era was declared closed and the **V2-COMPOSITE**
> acceptance vehicle launched, **ESCALATED at R1** (7 vs 6). The ledger's last entry is that
> composite's R2 running; **no R2 verdict was ever journalled**, and the newest run artifact under
> `Benchmarks/_ask_loop/runs/` is dated 2026-07-09. The bullets below are left exactly as written
> on 2026-07-06 — read them as history, not as the current queue.

- **In tree (trunk-v2 + banked):** attachment tools + paging + parties directive, find_person v2
  (signature/unlinked/domains + usage directive), participants[], date_span, doc-note,
  BG-vocab-in-description (MUT5, rung 2 in flight).
- **Queue (Codex-reconciled):** V2-MUT6 completeness-aware enumeration → reader-oracle probe →
  V2-MUT7 unified counterparty rollup → person-observation store (immutable, span-grounded) →
  vector/hybrid tool for synthesis walls → FTS/Bulgarian stemming.
- **Open strategic fork (founder decision pending):** small-model + person-centric DB enrichment
  vs bigger reader arm. Decision instrument: the reader-oracle probe (≥90% w/ gold evidence ⇒
  restructure DB; material failure ⇒ test bigger model).
- **V2 plateau counter: 5 rejected since CKPT2 (MUT0, MUT1, MUT3, MUT3b + one pending), cap 20.**

---

## 7. Era 3 — The EXP-001 lab convergence campaign (2026-07-06, full day)

**Provenance note:** this section records the SIBLING campaign run in the
`Experiments/Loops/EXP-001-schema-loop` lab world (bind-mounted `app.py` tool stack,
`exp001-lab-db`, benchmark = `Benchmarks/benchmark-email-only.json`, 43 founder-cleaned
email-answerable questions with per-question gold `method` recipes). It is written here in
full because its findings directly instruct this ask-tools loop. Authoritative ledger:
`EXP-001-schema-loop/archive/CAMPAIGN-1.md` — if this section and that ledger disagree, the
ledger wins. All scores below are judged by 4 parallel judge agents with SQL evidence per
verdict, every pass operator-verified against the live DB; anchor auto-verdicts are NEVER
trusted (measured false in BOTH directions repeatedly this day).

### 7.1 Corpus refresh (founder: "refresh and go with optimization cicle")

- Delta-ingested the founder's July-4 v2 export: **+89 inbound emails → 5,982 total, corpus
  now ends 2026-07-03** (export has no Sent/ folder → July outbound absent; recorded
  limitation). Idempotent by message_id; every row marked `labdelta-` for audit.
- All 7 projections rebuilt; rolling windows re-anchored to pinned-now 2026-07-04.
- **Bug caught at rebuild:** the supplier-exclusion pattern `%ДОСТАВЧИК%ЕТЕРА%` was
  order-blind (matched any genuine vendor bill listing Etera as RECIPIENT further down) —
  it had silently flipped craftgenie.ai vendor→client-engaged. Fixed with a 10-char
  proximity regex; registry re-verified (craftgenie=vendor, apis=client-engaged,
  mari.kareva=internal, tlyubenov=vendor-DOUBTFUL preserved).
- **Bug caught via judged autopsy (the D34.3 trail):** `lab_company_activity.emails_last_30d`
  used a stale hand-written date literal = actually a 59-day window; it served "ocenki: 71
  emails last-30d" where corpus truth was 13. The reader NOTICED the contradiction against
  per-person data and STILL trusted the tool. All builder windows now DERIVE from
  LAB_PINNED_NOW arithmetic. Law: **readers trust projections over their own cross-checks —
  projection fidelity is load-bearing; hand-written window literals are banned.**

### 7.2 Refreshed baseline = Gemma-4-31B 12/43, fully adjudicated

The prior round's 16/43 did NOT reproduce; the decomposition (all judged) matters more than
the number:
1. **Grading contamination found+fixed:** `regrade.py` built its gold map from the OLD dev
   question file only — email43 answers were anchor-graded against the WRONG golds, and the
   18 new benchmark ids were silently skipped. A full audit of the auto-fail bucket rescued
   FOUR correct answers (A1.1, A2.1, A10.1, D27.1) and overturned one false-pass (G69.2).
   **Rule banked: no auto-graded bucket is ever trusted without a judged audit.**
2. Two Together API kills (E40.1 outage turn 9, E43.1 429 turn 10) produced empty answers;
   both were retried under an infra-fairness policy; both retries then failed on substance
   (method mismatch / wrong-program misattribution) — the outages masked nothing.
3. Judging strictness was aligned to the established calibration precedents (E39.3 bare-punt
   rule, B15.1 thin-answer rule): thin-but-true answers fail consistently now.
4. The two formerly time-quarantined questions (A2.1, A7.1) both PASSED — the corpus refresh
   did exactly what it was for.
5. First-round verdicts were reconstructed into the graded cache so historical runs
   reproduce their ledger scores (Gemma 16 = 6 new-id + 10 overlap; 9B 10/43).

### 7.3 Single-agent mutation wave — M-19…M-23 (Gemma), ceiling declared

| Mutation | What it was | Rung result | Verdict |
|---|---|---|---|
| M-19 `LAB_BEHAVIOR_V4` | anti-false-negative protocol: mandatory both-language + attachment-text + subject-line probes before any "no data"; Yes/No-first for existence questions; nearest-evidence reporting instead of bare punts | 1/8 flips (A3.3) | KEPT |
| M-20 `LAB_DOC_WINDOWS` (+`LAB_DOCWIN_CHARS`, + document_head) | `get_document_window`: keyword-centered excerpts from the FULL attachment text (±500→±900 chars) + a head excerpt (title/parties block). Mechanism find: the Vetera contract's payment section sits at char ~12,300 of 26,199 — head-truncated previews made it UNREACHABLE BY CONSTRUCTION | 0 flips but mechanism-verified: D31.3 went from "terms don't exist" to the full 19,817 / 7,927+trigger / 11,890+trigger structure; failed only the gold's Tridea-counterparty gate. FP8 variance then picked a January DRAFT contract on the next roll — closed per anti-flip-chasing | KEPT (infrastructure) |
| M-21 `lab_sent_offers` + `list_offers` | exhaustive pre-computed outbound proposals/offers/contracts enumeration (D25.1 mechanism: ad-hoc search enumerated 1 → 5 → 0 proposals across three runs — recency-window collapse then a false no-data) | see M-22 (they converged) | KEPT |
| M-22 `LAB_PAYLOAD_UTF8` | **campaign-defining find:** the runner serialized every tool payload with ensure_ascii=True — every Cyrillic char cost 6 payload chars = a ~6× cap tax on a Bulgarian-dominant corpus, in force ALL CAMPAIGN, plus silent mid-row truncation with no marker (the model relayed everything it received and had no signal rows were missing). Fix = raw UTF-8 + explicit truncation marker | **D25.1 FLIPPED — the full coverage chain: baseline 1 → M19 5/21 → M21 1/21 (output-token cap) → 8K rerun 7/21 (input payload cap) → UTF-8 18/21 PASS.** Fixing each bandwidth layer revealed the next | KEPT |
| M-23 `LAB_METRICS` | pinned metric recipes: threads = lab_thread; unique totals = union−overlap; rankings need last-activity/dormancy checks; responsiveness = replies not volume; density = per-week over ≥30d spans | 1/5 flips (A9.1 = "2,492 threads [lab_thread]", operator-verified exact) | KEPT |

**The ceiling law:** recipes and projections ROUTE, they don't COMPUTE. G62.2 produced the
same 442 overlap double-count in FIVE consecutive variants, including one where the
union-minus-overlap arithmetic was spelled out in its prompt. D29.2/E40.1/D34.3 adopted the
mandated metric shapes and then failed the computation inside them (skipped the dormancy
check, chose CC-inflated attribution, undercounted the reply field). **Gemma single-agent:
14/43 gate level (32.6%), 16/43 union. Full-43 confirmation gate showed FP8 churn swaps the
marginal band run-to-run (A3.3 out, D32.2 in) — the level holds, the composition shuffles.**

### 7.4 M-24/M-24b — the split arm, and the verifier law

Founder-mandated router/specialists rematch, built as a VERIFICATION layer (Campaign-2 law):
one generic no-tools router call classifies RETRIEVAL / COMPUTE / SYNTHESIS → flat agent /
SQL-first contract / draft-then-verify.
- **Verify pass (no tools): fixed the SIGNATURE ERROR 3/3** — B14.3's cancellation polarity
  (inverted for four straight rounds), D34.1's register framing, E43.1's wrong-program
  misattribution — **but zero flips: verification ≠ coverage** (it audits gathered evidence;
  it cannot fetch what the researcher never read).
- **Verify WITH probes (M-24b): D34.1 FLIPPED** (all 3 arc anchors: the 15.5-month silence,
  the 2026-01-13 re-engagement, the March commitment — invoice 0000000017 = 3,600 лв Брийз,
  operator-verified exact). **But E43.1 REGRESSED WITH A NEW FABRICATION: the probing
  verifier invented a "relationship expansion" out of the very thread containing the
  2025-05-22 CANCELLATION — inverting a went-wrong into a went-well WHILE VERIFYING.**
- Compute scaffold: bypassed whenever projection tallies were available (path of least
  resistance); a mechanical enforcement turn was added. The router misrouted G62.2 to
  RETRIEVAL three times ("find all emails mentioning..." phrasing masks the count crux).

**The law (revises "multi-agent pays in verification"): verification pays only when the
verifier is STRONGER than or DECORRELATED from the reader. A same-strength verifier
inherits the reader's own failure modes.** The split converts COORDINATION failures (arc
assembly) but cannot manufacture COMPETENCE (arithmetic, quantified-outcome retrieval).
**Gemma final incl. split: union 17/43 (39.5%).**

### 7.5 OPUS-ARM-2 — the reader gap on the finished harness (founder-ordered)

Six strong-reader (Opus-class) subagents on the IDENTICAL tool stack (tool_cli shim, same
payload caps, UTF-8 parity), gold-stripped, all 43 questions, judged by the same judges
under the same bars, every pass operator-verified: **26/43 (60.5%).**
- **Reader gap: +12 questions (+28pp) on identical tools.** Opus-only wins (11) were
  exactly the diagnosed Gemma competence classes: the 421 union arithmetic (0/5 for Gemma,
  clean for Opus), 72/72 enumeration (Gemma found 6), delivered-AND-paid B14.3 (0/5 Gemma),
  full contract terms incl. the Tridea gate, thread-ranking with the dormancy split,
  density method with burst exclusion, role judgment (Marina = developer).
- Gemma-only wins: 2 (A3.1, D34.1 — Opus fell into the varshyp/display-name first-email
  trap). Cross-model union 28/43 (65.1%) — decorrelation is real, but the union number
  assumes an oracle selector that production does not have (Codex's correction).
- **Harness-induced fail class flagged:** the registry's tlyubenov "vendor-DOUBTFUL"
  honesty tag STEERED the strong reader to exclude the #1 external counterparty on
  E44.1/G69.1/G69.2. Tag semantics must split role-belief from billing-verified.

### 7.6 The bandwidth arm — the database-architecture answer

Founder question: "opus did it from the raw email JSON once — does our infrastructure not
provide enough?" The 17 Opus fails were rerun with HONEST infrastructure — 32K payload
caps (vs 8K tuned for the 9B), 100-result search limits, richer previews/doc windows — plus
two generic prompt corrections (role tags are HINTS not verdicts; blank-sender/display-name
identity). **8/17 FLIPPED → strong reader = 34/43 (79.1%).**
- Bandwidth-bound (5): A3.1, E43.1 (the 2025-03-18 "чудесното обучение" praise lives INSIDE
  the Фактура-2 rejection thread — shallow reads structurally missed it), E41.2, G62.3,
  D34.1-depth.
- Tag-steering (3): E44.1, G69.1, G69.2 — Tihomir included in all three once tags became
  hints, judged from actual email content.
- Genuine residue (9): 4 PROJECTABLE — A4.1 document-canon (the real deck has 33 copies; no
  tool ranks attachments by ubiquity), A4.2 + A9.2 person-identity/entity-merge (person_email
  is 1:1; lab_org never merged gbs-is/gbs-sofia), E41.1 commitment-extractor (promise →
  outcome tracking) → realistic frontier ceiling ≈ 38/43 (88%). 5 REASONING-BOUND — C22.1
  stakes judgment (recognized the client meeting, still called it skippable), **D34.3 volume
  fallacy where the reader FABRICATED a dismissal ("brand-new contacts") of the true risers
  who demonstrably had prior traffic**, E41.3 full quantification, G65.1 sweep breadth,
  G65.3 false-positive diagnosis.
**Conclusion: the DB always had the data; the ACCESS SHAPE was the constraint.** Laws:
payload caps must scale with reader class (one cap for all models loses ~19pp on a frontier
reader); triage questions (read-N-and-classify) need bulk evidence reading or map-reduce;
projections absorb computation on every reader class. Profile persisted:
`EXP-001-schema-loop/harness/runner/STRONG_READER_PROFILE.md`. tool_cli default caps raised
to the strong profile; the two data-judgment rules made default-on in the system prompt;
the Gemma-at-32K default decided by a rung (in judging at close).

### 7.7 Gold-method mining + the folder dimension

All 43 gold `method` fields catalogued (founder-prompted). Universal author shape:
**load-everything → dedup by message_id → compute over the whole corpus → read the hits**
(corpus-as-dataframe) vs our search-then-read. Confirmed already-absorbed: dedup at ingest,
real bodies (97.8%), both-scripts search, union-find threads, pinned-now windows, org
merges, deep-attachment reading, exhaustive offers. Two unmined:
1. **The FOLDER dimension — used by ~10/43 golds** (status from Clients/Frozen/..., vendor
   roles from Accountancy/Bulbank dirs, client attribution from paths, Sent-split, the
   no-Trash proof). **`lab_folder` BUILT same day** from the export tree: 6,263 rows, 99.7%
   joined to email_message, client tokens land exactly on the benchmark entities (Ocenki
   392, GBS 199, APIS 112...). The Opus arm discovered and used it mid-run (A3.3 asterisk).
2. Payment-advice extraction (`lab_payments` — the "money landed" dimension B14.3 needed
   five times) — designed, still unbuilt.

### 7.8 The SQL-specialist lane (founder: "did we provide the Ollama SQL agent?")

Answer: NO — `XiYanSQL-QwenCoder-7B` sat installed on host Ollama (founder installed it 35h
earlier), never wired. The founder's no-local-model rule targeted slow 12-turn loops; a SQL
specialist writes one short completion per question. Wired as:
- **M-25** (`LAB_SQL_SPECIALIST`): specialist writes ONE query per COMPUTE intent, the DB
  executes, the reader phrases results marked AUTHORITATIVE.
- **M-26** (`LAB_SQL_PLANNER`, the founder's design): Gemma-as-leader fills a STRUCTURED
  slot plan (goal/metric/entity/window/split — form-filling, never free-form task prose,
  because free task-specification is exactly where the 31B fails), each step goes to the
  specialist WITH the original question visible (a weak leader's paraphrase would launder
  its misunderstanding through the specialist's competence).

**Founder-prompted MANUAL testing of the specialist found 3 harness bugs:**
1. Hand-written schema cards shipped wrong columns TWICE (`thread_id` on email_message;
   `first_at` on lab_thread). Fix: SPECIALIST_DDL is now GENERATED from information_schema
   at process start. Law: **never hand-write schema documentation shown to models.**
2. **`sql_query` silently rejected every `LIKE '%x%'` pattern ALL CAMPAIGN** — psycopg
   parses `%` placeholders whenever a params sequence is passed, even an empty one. Every
   reader arm paid this tax whenever it wrote pattern-matching SQL. Fixed (execute without
   params when none).
3. The specialist output guard rejected valid WITH-CTEs; then a double-escaped regex in the
   fix disabled the lane entirely for the first M-26 rung. Fixed with startswith.

Manual specialist quality after fixes: **A9.1 → 2,492 exact · G62.2 → 421 exact (the
arithmetic the 31B failed 5×, solved one-shot by a local 7B) · G65.3 → 61 exact · clean
lab_folder joins.** Fails one-shot 3-table entity-bridging (threads-per-client-domain) —
the case for M-26 decomposition or Codex's bridge table.
**Provenance finding from the broken-lane M-26 rung: with the specialist accidentally
disabled, Gemma ALONE (schema card + %-LIKE fix + V4/METRICS prompts) produced the
gold-accepted D34.3 answer — Anton Lin, 5 vs 1 replies — the first correct D34.3 across
ALL arms including both Opus runs, plus a dormancy-annotated D29.2 thread ranking. The
%-bug fix + honest schema may matter more than the specialist itself for the sovereign
arm. JUDGED (16:04 UTC): BOTH PASS — D34.3 operator-verified exact (Anton Lin 5v1), the
first correct answer across ALL arms; D29.2 dormancy satisfied via last-activity dates.
Gemma union → 19/43 (44.2%). Two infrastructure-honesty fixes did what five prompt/scaffold
generations could not. LATER SAME DAY (step-anatomy sweep of all 28 gate-fail transcripts,
founder-prompted): ~half of scored reader-failure = harness defects (total_matches was a
per-term SUM feeding the 442; the vendor-DOUBTFUL payload verdict; silent 15-row caps;
find_person other_addresses=null; retry-bait error text — 160 calls burned on CRM-only
Tierrasoft). total_matches fixed to deduped union (421 served); tag reworded to
billing_unverified hint. D31.3 gate answer ruled a GRADING ARTIFACT → PASS →
**final: GATE 15/43 (34.9%), GEMMA UNION 20/43 (46.5%)**. Anatomy fix queue + behavioral
laws (passes 3 calls/31s vs fails wandering 5/45s; projection tools 2x in passes) in the
lab ledger. Cross-read of THIS diary sections 0-6 adopted into the lab loop: the
CHANNEL-CONSISTENCY LAW (retest prompt-channel mutations as tool changes), N=3 majority
floor, the reader-oracle probe, code-sha run metadata.**

### 7.9 Cross-vendor consults (Codex/GPT-5.5, headless exec)

**Strategy consult** (ranked E>A>B>C>D):
1. Build a risk-aware ROUTING + ABSTENTION layer first — optimize trustworthy production
   coverage, not raw benchmark accuracy; "insufficient evidence" as a first-class answer;
   HITL escalation for ambiguous judgment. The D34.3 fabricated-dismissal matters more than
   several ordinary misses — confident false synthesis is the product-killer.
2. Build the 4 projections, then demonstrate the ~88% on an UNTOUCHED run (never declare
   the arithmetic).
3. Productionize the hybrid (frontier reader + SQL lane + projections) as default Ask.
4. Time-box sovereign Gemma work; re-score it on SAFE COVERAGE (answer-or-abstain quality).
5. Ensemble only on low-confidence/disagreement (union assumes an oracle).
STOP: optimizing prompts against these same 43 questions — **email43 is FROZEN as a
regression suite**; new claims need unseen questions. MEASURE NEXT: risk-weighted selective
accuracy vs autonomous coverage on a blind stratified set (% answered / verified correct /
unsupported-claim rate / % escalated / cost, per deployment path).

**DB-architecture consult:** projection-heavy direction CONFIRMED; normalized schema stays
the system of record with a deliberately denormalized, VERSIONED read model. Four layers:
canonical source → entity/evidence (people, companies, threads, canonical documents,
commitments, provenance) → agent projections (narrow, indexed, tool-shaped) → semantic
tools (get_entity_dossier, batch_get_messages, count_term_hits, get_open_commitments — the
LLM should never construct 3-table joins). ONE intentional bridge fact table
`message_party_fact` (org_id, email_id, sent_at, party_kind, address_norm, domain_norm,
person_id, company_id, thread_id, direction, ACL key, resolver_version) — kills the
entity-bridging class that defeats the 7B and taxes everything else. Promote lab_* to
production tables with org_id NOT NULL + projection_version + computed_at +
source_watermark; security_invoker views as stable contracts (views are interfaces, not
compute engines); NO GraphRAG now (freshness/provenance/ACL cost without addressing the
measured bottleneck).

### 7.10 Final scoreboard + what transfers to THIS loop

| Arm (email43, same judges, same bars) | Score |
|---|---|
| Qwen 9B | 10/43 (23.3%) |
| Gemma-4-31B max harness (level / union, final after anatomy sweep) | 15/43 (34.9%) / 20/43 (46.5%) |
| Opus-class @ 8K small-reader caps | 26/43 (60.5%) |
| **Opus-class @ honest infra + tag/identity fixes** | **34/43 (79.1%)** |
| Cross-model union (oracle-selected, aspirational) | 28-35/43 |
| Realistic frontier ceiling after 4 residual projections | ~38/43 (~88%) — must be shown on an untouched run |

**Direct transfer queue for backend/app/ask (this loop):**
1. **Check `shared_core.py` for the psycopg %-placeholder bug NOW** — same library, same
   pattern, likely the same silent LIKE-rejection tax.
2. Payload caps must scale with reader class; a single small-reader cap costs a frontier
   reader ~19pp.
3. Generate every model-visible schema surface from information_schema — never hand-write.
4. Adopt the two data-judgment prompt rules (tags-are-hints; multi-address identity).
5. Codex's `message_party_fact` bridge + versioned projection promotion when Ask goes
   production.
6. The four projections before any further prompt campaign: folder/filing metadata at
   ingest, payment-advice extraction, document-canon, person-identity merge.
7. email43 frozen as regression; author the next gold set blind and sealed; measure the
   selective-accuracy curve (answer/abstain/escalate) — that curve, not a score, is the
   production decision instrument.

---

## 7. Diary re-read findings (2026-07-06 evening — mined from this file itself)

**7.1 The empirical per-question stability table** (all v2 runs pooled, ≥4 episodes; caveat:
pools configs — V027 is 33% pooled but 6/6 under the current tree with paging+parties; V041 is
28% pooled but 5/6 under vocabulary configs):

| Class | Questions | Use |
|---|---|---|
| STABLE-PASS (≥85%) | V001 V002 V003 V005 V006 V015 V023 V033 | the ONLY legitimate guard set |
| FRAGILE (30–85%) | V032 (75%), V030 (50%), V027* (config-dep), V041* (config-dep) | never guards; candidate gains need unanimity or probes |
| SCATTER-LOW (5–30%) | V019 (10%), V026 (8%) | capability exists, fires rarely — validator/retry targets |
| STABLE-FAIL (0%) | V007 V008 V011 V013 V018 V021 V024 V025 V029 V031 V036 V039 V042 V043 | the honest attack surface (14q) |

**7.2 The "+1 graveyard" implication:** every single-piece candidate in both eras landed +1/+2 and
died; the only accept ever (MUT5) was a four-piece composite. → The V2 acceptance vehicle should
be the COMPOSITE of individually-proven pieces (vocab-in-tool + enumeration + counterparty +
banked stack), not another single.

**7.3 Self-violation check:** the standing prompt suffix stacks THREE procedures (citation +
authored-counts + timeline) despite Lesson #4 (procedure interference). Never ablated on v2.
→ Candidate: citation-only prompt ablation now that most content lives in tool descriptions.

**7.4 Priority inversion:** the reader-oracle probe (the strategic fork's decision instrument) is
cheap and still unrun — it should precede further mutation spend, not follow it.

**7.5 External input — Recursive Language Models (arXiv 2512.24601, MIT CSAIL, founder-supplied
2026-07-06):** treat long content as an external environment + let the model DELEGATE reading to
recursive sub-model calls over slices; env-alone helps long inputs, sub-calls dominate on
information-DENSE tasks; root-strong + sub-cheap is the cost-optimal split. Derivations adopted:
(a) our SQL-tool architecture = the RLM thesis, validated; (b) our missing primitive is
delegated reading → **V2-MUT8 candidate: `extract_from_messages(filter, instruction)`** — executor
fans sub-LM extraction calls (span-grounded, id-carrying) over full bodies and returns structured
rows the root reader synthesizes from; attacks synthesis (V021/V029/V019), enumeration, and
noise-filtering (V039) walls simultaneously; (c) the strategic fork is three-way: precompute
(person-DB) vs delegate-at-query (RLM sub-calls) vs bigger reader — and (a)+(b) compose: the same
extraction sub-calls run at ingest = the person-observation store generator. Sub-call outputs are
tool payloads = the deterministic channel (Lesson #1 compatible).


### 7.11 Addendum (2026-07-06 night): ASK-02 mechanical fixes applied to shared_core.py
Founder-relayed ASK-02 findings verified live (all 5 CONFIRMED; see the lab ledger).
Applied to backend/app/ask/tools/shared_core.py: F6 MIME-family map (+filename-extension
fallback) — content_type=docx returned 0 of 805 real Word files, now returns rows; F7
always-on language_coverage envelope note (question-blind single-script detection — the
old translate hint fired ONLY on zero results, so 40 English hits masked 395 Cyrillic);
F2 participants_all AND-filter + schemas + guard (pair-correspondence now expressible;
verified 80 exact). NEW GENERAL LESSON for this loop: the NULL-ILIKE trap — ILIKE on a
NULL column inside NOT(...) admits every NULL row (measured 3,131 false matches); coalesce
every text filter used under negation. Conformance/N=3 re-validation of these tool changes
belongs to this loop's own ladder before any accept is claimed. F1 (rollup primitive,
security_invoker-only per F12) and F10 (abstention gate) remain with the ASK-02 design doc.

### 7.12 (2026-07-25): Codex full-branch review → 10-Opus verification → fix wave
Founder ran a Codex (GPT-5.6-sol) review over the whole branch: 22 findings = 20 distinct
claims (2 duplicate pairs). Ultracode verification: 10 isolated Opus verifiers, one per file
group, executable repros where decisive. Verdict: **16 CONFIRMED / 4 QUALIFIED / 0 REFUTED** —
the strongest cross-vendor pass yet; every claim had substance. All confirmed defects fixed
same-day (uncommitted, this working tree):

- **sql_guard.py** — rewritten around ONE literal-aware lexer ('' doubling, E'…', $tag$…$tag$,
  "…", nested /* */): comment stripping, forbidden scan, SELECT INTO, single-statement, and the
  M-49 DESC rewrite all run on masked text. Kills N1 (P1: ~19 common English words in ILIKE
  literals made query_database unusable — '%update%', '%call%', '%security%'…) and R7 in the
  root ($$desc$$ was being rewritten INSIDE the literal; '--' inside literals truncated the
  statement).
- **registry.py** — every executor dispatch now runs in a SAVEPOINT (begin_nested). N2 (P1):
  one failed generated-SQL statement aborted the whole session transaction (25P02) and every
  later tool call died; observed cascading in 14 real xiyan transcripts. GUC scope verified
  savepoint-safe.
- **agent_runner.py** — blind 6k string slice replaced by structural truncation (_fit_payload):
  drop list tail rows → shorten longest strings → hard slice only as pathological fallback;
  always valid JSON, always an explicit `truncated` marker, a cut listing flips
  listing_complete. Plus per-question usage via a sink (N11: shared-counter deltas over-counted
  by up to the parallelism factor).
- **shared_core.py** — envelope reordered (control fields FIRST, results LAST — R2/N4 P1:
  the slice was cutting exactly the fields the descriptions promise); participants_all is now
  per-party ALIAS GROUPS ((a_old|a_new) AND (b_old|b_new) — R1); >4 parties / >8 aliases RAISE
  instead of silent trim (R3/N14: dropping an AND conjunct widens); NULL-safe subject ranking
  (N3: NULLs-first under DESC floated subject-less rows to the top of every participant-only
  page); latest-boundary id DESC tie-breaker (R5); sent_split_note states the mailbox reference
  frame (R4: direction is mailbox-relative, NOT who-wrote-to-whom); 0-match listing_note;
  not-found envelopes no longer echo the requested id (N5 laundering path).
- **router.py** — kits reconciled with the live registry (phantom counterparty tools removed,
  get_attachment added to content_search/synthesis, group_by phrasing gone); registry_for_class
  RAISES on unknown kit names; content-channel classification now bare-match-or-last-mention
  (N13: dict-order first-match returned classes the model explicitly rejected; ROUTED2 had
  collapsed to entity_lookup 33/33).
- **sql_pipeline.py** — compose calls inside the fall-through try (N10: a ReaderModelError from
  compose escaped and killed the whole run); ONE rows string is both composer input and stored
  payload (N7: two different 6k windows made the grader call genuine tail-row citations
  invented).
- **run_eval.py** — cache key now includes person/org + per-question text sha (N8, P1: a
  person-bound run and an unbound probe shared cache files — the permission probe measured
  nothing; ALL existing caches intentionally invalidated); gather(return_exceptions=True) →
  one bad question = one error record, engines disposed only after all children settle; meta
  records arm/parallel/errored_questions.
- **grade.py** — citation evidence from result_payload ONLY (N5, P1: a model-invented UUID
  passed as a tool ARGUMENT counted as evidence and defeated the anti-fabrication gate);
  no-data zero-regex left-boundary (N6: '10 emails' matched '0 emails'); uniform ungraded row
  + tolerant tally (N9). New conformance pins for all three.

**⚠️ ARM-VERDICT CORRECTION (supersedes §"MUT12" PARK reasoning):** the routed-arm park number
(18.2% vs 24.2%) was measured with 4 of 6 kits silently crippled by phantom tools and
procedures pointing at a non-existent count_emails(group_by=…) (N12). The park DECISION stands
(don't spend on routing now) but the MEASUREMENT is confounded — any future routing revival
must re-run the arm on the reconciled kits before citing that number.

QUALIFIED-only (no code change): R6 MIME family map is documented intent (now stated in the
content_type description); N15's "signature logos counted as documents" mechanism was measurably
wrong on this corpus (is_inline=true is 0.28% of rows — the 57% logo noise carries NO
Content-Disposition:inline header), fix filed as ASK-FBP items instead.

### 7.13 (2026-07-25): second pass — 6-Opus review OF THE FIX WAVE
The fixes themselves went through an adversarial review (6 Opus lenses: guard lexer security,
shared-core SQL, runner/savepoint, eval harness, test quality, cross-cutting integration).
Result: **3 P1 + 13 P2 + 17 P3** — i.e. the first fix wave shipped two NEW security holes. The
lesson for this loop: *a fix wave needs the same adversarial pass as the code it fixes.*

- **P1 — GUARD BYPASS (regression introduced by the R7/N1 fix).** The forbidden-token scan was
  moved onto the literal-masked text, but the mask also blanks QUOTED IDENTIFIERS — so
  `SELECT "set_config"('app.current_person_id', …)` passed the guard verbatim, while HEAD had
  rejected it. Combined with dispatch's new savepoint (a released savepoint keeps its side
  effects) that is a person-GUC widening path: tool call 1 sets the GUC, tool call 2 reads
  another person's mail. **Fixed:** the lexer now emits THREE position-aligned views —
  `masked` (drives DESC rewrite / ';' / LIMIT), `code` (string literals masked, identifiers
  VERBATIM — drives the forbidden + SELECT INTO scans), `stripped` (executable).
- **P1 — phantom dollar quote.** `$` is an identifier continuation char in PostgreSQL, so
  `a$$b$$` is ONE identifier; the lexer read it as a dollar-quote opener and, finding no
  closing tag, masked the whole rest of the statement — hiding set_config, `;` and DML from
  every check, and silently voiding the M-49 DESC normalization. **Fixed:** identifier runs are
  consumed as whole tokens before the `$` branch, an unterminated literal/comment now REJECTS
  (fail-closed instead of masking to end-of-input), and DESC/LIMIT are found by TOKEN SPAN
  rather than regex (`\bDESC\b` also matches inside `money$$desc$$`).
- **P1 — _fit_payload trimmed by key NAME.** On a get_email result it emptied `recipients` and
  `attachments` (both promised complete by the tool contract) to protect a giant `body_text`
  it then shortened anyway. **Fixed:** trimming is driven by SIZE CONTRIBUTION — each round
  shrinks the single largest field; a shortened `text` also moves `next_offset` back to the
  real end of what the model saw, so paging can no longer skip unseen characters.
- **P2 fixes:** `date_span` had no id tie-breaker (the one field the description tells the
  reader to trust for every latest/earliest fact); `participants_all` still silently dropped a
  BLANK party entry (same widening direction the cap check exists to prevent) and the OR side
  (`queries`, `participants`) still trimmed in silence — all three now raise; the honesty gate
  now requires the ABSENCE of a data assertion, not merely the presence of a negative phrase
  ("I found 10 emails … but no attachments were included" used to auto-pass a no_data gold);
  errored records grade as `error`, never as a model `fail`; sql_pipeline got the usage sink
  and UUID-redacted SQL echo; the cache hash now globs the whole tools package.
- **A2 file-size gate.** `shared_core.py` had reached 1035 lines (ceiling is 500, CI-enforced;
  it was already 558 at HEAD). Split into `tool_helpers` / `email_filters` / `person_tool` /
  `email_search` / `email_read` / `attachment_tools`, with each tool's ToolSpec living beside
  its executor and `shared_core.py` reduced to registry assembly (41 lines). Test module split
  to match. Import path `app.ask.tools.shared_core.build_shared_core_registry` is unchanged.
- **New pins:** guard-bypass regressions (quoted identifier, `a$$b$$`, unterminated literals,
  `money$$desc$$`), the _fit_payload P1 + next_offset + no-caller-mutation, the raise cases,
  and — first coverage ever — a LIVE savepoint containment test (`test_registry_dispatch.py`),
  proven non-vacuous: the same sequence without the savepoint reproduces SQLSTATE 25P02.

### 7.14 (2026-07-26): red-team round 1 — a WORKING tenant escape, found and closed
Standing goal from the founder: keep running adversarial Opus agents (including agents whose
explicit job is to HACK the system) plus Codex until nothing critical or medium remains.

**CRITICAL — unicode-escape identifiers defeat any name-based denylist.** PostgreSQL decodes
`U&"…"` escapes, so `U&"\0073et_config"` IS the identifier `set_config` while the raw text a
token scan sees is backslash-digits. Demonstrated end to end on a live PostgreSQL 16, **as the
restricted `oneai_reader` role**:

```
SELECT U&"\0073et_config"('app.current_org_id', '…0009', true)   -- guard: ACCEPTED
→ current_setting('app.current_org_id') = '…0009'                -- org scope hijacked
→ same for app.current_person_id                                 -- person scope hijacked
```

Because `registry.dispatch` RELEASES its savepoint on success (verified: rollback restores a
GUC, release keeps it), the rewritten scope would persist for every later tool call in the same
question — full cross-tenant read. This is exactly the PF-FBP-8 hazard, reached around the
denylist rather than through it.

**Two fixes, one of them structural.** (1) The lexer now refuses `U&'…'` / `U&"…"` outright —
the SQL specialist never emits it and over-rejection merely falls through to the agent.
(2) More importantly, **all generated SQL now goes through `app/ask/tools/sql_execution.py`**,
which snapshots `app.current_org_id` + `app.current_person_id`, executes, and then proves the
statement did not move them — restoring the scope and failing the call if it did. That makes
the whole CLASS non-exploitable rather than this one instance: the denylist is a filter over a
language PostgreSQL parses and we do not, so every acceptance is now provisional.

**The lesson to carry into MCP-01:** a denylist over SQL text cannot be the only defence. The
structural check (prove the bound scope survived) is what actually holds when the filter is
wrong — and the filter WILL be wrong again. Its test suite includes a case that stubs the
guard out entirely and still requires the call to fail.

**The backstop paid for itself within the hour.** The red-team round returned a SECOND,
independent escape: `query_to_xml('SELECT set_config(…)')` executes SQL from a *string
argument*, which no keyword denylist can read. It was found by a different agent, on a
different attack path — and the scope check already stopped it (verified in both is_local
forms, scope restored). One structural control, two unrelated bypasses neutralised.

**Round-1 findings beyond the two escapes** (all verified personally before acting):
- **Three CRITICAL evidence-laundering paths**, each turning caller-supplied text into
  "tool evidence": `per_term_matches` keys echo the model's own search terms, `UnknownToolError`
  echoed the model-chosen tool NAME, and `_parse_iso_date` echoed the rejected argument. Any
  of them mints a citable uuid with no database access at all. Closed by not echoing at all
  where the text has no value (tool name, bad date) and by `redact_uuids()` where it does
  (search terms, echoed SQL) — with the redaction regex now matching the grader's extractor
  **character for character**, since a redaction narrower than the extractor is not a redaction.
- **The hatch's reach was the reader ROLE's grant list (16 tables), not the documented six** —
  `audit_log` (actor emails, IPs), `connector_connection` (mailbox usernames, secret
  ciphertext) and `principal_source_identity` were all reachable by free-form generated SQL.
  Now every statement is EXPLAINed first and rejected unless every relation it plans to touch
  is in the allowlist. The planner is the right oracle here: it sees through views and
  subqueries, which no text scan can.
- **Two measurement-integrity CRITICALs in the grader:** the citation gate only fired on rows
  the deterministic grader had already passed, so free-text golds (the class where provenance
  matters most) could carry wholly invented citations to the critic; and the cache key ignored
  which DATABASE and reader endpoint produced the answers. Fabricated citations now fail in
  every state, and the corpus + endpoint are in the cache basis.
- **A stale-gold join:** results bind to gold by `qid` alone, so editing a question's text
  re-pointed stored results at a gold they never saw. Grading now compares the stored question
  text and reports `stale` instead of scoring it.

### 7.15 (2026-07-26): I broke my own backstop — and it changed the design
While round 2 was running I attacked the scope check I had just written, and it fell:

```sql
SELECT (SELECT set_config('app.current_org_id','ORG-B',true)) AS hijack,
       (SELECT string_agg(secret,',') FROM probe_rows)        AS leaked,
       (SELECT set_config('app.current_org_id','ORG-A',true)) AS restore;
-- leaked = 'B-secret'  (the OTHER org's rows)
-- scope after the statement = 'ORG-A'  -> the before/after check sees NOTHING
```
Verified on live PG 16 with a real RLS policy and the `oneai_reader` role. **A before/after
comparison cannot secure a boundary that the statement is free to cross and re-cross between
the two observations.** Two more shapes work the same way (a set-returning function in the
FROM clause, and a CTE).

**The redesign: review the PLAN, not the text.** PostgreSQL renders a query plan CANONICALLY,
and that turns out to be the right place to stand:
- `U&"\0073et_config"(…)` — the unicode-escape bypass — appears in the plan as plain
  `set_config(…)`. Every lexical evasion is already undone before we look.
- A call buried in a scalar subquery gets its own InitPlan node, so hijack-and-restore is
  visible even though its net effect is invisible.
- `query_to_xml` shows up as a `Function Name` / `Function Call`, so a payload the planner
  never parses is still blocked by the NAME of the function that would run it.
- Relations are named after view expansion, which no text scan can do.

So `execute_guarded_sql` now EXPLAINs every statement and refuses it unless every relation is
allowlisted AND no forbidden function is called; the text guard is demoted to a cheap first
filter, and the scope check is demoted to a **tripwire** with its docstring corrected to say
so. Its test now stubs BOTH front-line checks — otherwise it would only be re-testing the
plan review. Verified: 8/8 known attacks rejected (including all three hijack-and-restore
shapes with the text guard disabled), 6/6 legitimate queries still accepted, including
searches whose LITERALS contain `set_config` and `update`.

**The general lesson, worth more than the fix:** when a control compares state before and
after an operation, ask what the operation can do *in between*. If the answer is "anything",
the comparison is a tripwire, not a boundary — and it should be documented as one so nobody
later mistakes a passing check for proof of safety.

### 7.16 (2026-07-26): regression armour — so a fix can't quietly break a fix
Three times in two days a repair introduced a new defect (two P1s in the first fix wave, an
over-rejection in the second). Passing tests did not prevent any of them, because each new
defence was tested by the test written beside it. Two artifacts now close that gap.

**1. The attack corpus is DATA** (`tests/ask/security/attack_corpus.py`). Every attack that
has ever worked, with its provenance and the guarantee it protects; the tests iterate the
corpus, so coverage cannot be lost by rewriting a test function. It ships with an equally
load-bearing **ALLOWED corpus** — legitimate statements that must keep answering — because
over-rejection is the other way this layer fails, and a hardening pass that quietly stops
answering real questions is still a regression. It caught one within a minute of existing:
searching for the literal `'%set_config(%'` had just become impossible.

**2. The defence matrix** (`scripts/ask_loop/defence_matrix.py`, printed in CI). It disables
each layer in turn and re-runs the whole corpus, answering the question a green suite cannot:
*is this layer load-bearing, or redundant cover — and is any attack held by a single thread?*

Running it immediately exposed a **hole in the primary control that every test had missed**:
`_plan_call_text` only read string-valued dict entries, but PostgreSQL puts expressions in
`Output`, which is a **list** of strings. So `Output: ["pg_sleep(30)"]` was never scanned —
the plan review was silently resting on the text guard in front of it. Fixed (recurse into
strings at every depth) and pinned by `tests/ask/tools/test_plan_scanner.py`, which asserts on
plan FIXTURES and therefore needs no database.

Matrix after the fix: every attack is stopped by at least two layers except the five
"reads a table outside the documented schema" cases, which only the relation allowlist can
know about — so that check is the load-bearing control for REACH, and a regression there is a
real escape. That is now written down rather than discovered later.

**The pattern to keep:** a defence is not verified by a test that passes. It is verified by
turning off everything in front of it and watching what still holds.

### 7.17 (2026-07-26): red-team round 2 — the denylist finally lost, and the grader was lying
Round 2 (5 attackers + a re-framed Codex pass; the round-1 Codex brief had been refused by the
provider for reading as an offensive request) returned 12 CRITICAL / 22 MEDIUM.

**The hatch: stop naming the dangerous things.** Two escapes, found independently:
`database_to_xml` / `schema_to_xml` dumped connector credentials, the whole `audit_log` and the
identity graph — the SPI dump is invisible to the planner, so the relation allowlist saw an
EMPTY relation set and was satisfied *vacuously*. And `SELECT '<uuid>' AS id` put a
model-authored constant into `rows`, where the citation grader read it as an id a tool
returned. One rule closed both: **the statement must actually read an allowed relation**. The
XML family is now a regex rather than a list, because the list "looked complete" twice and was
wrong twice. `lo_*` joined it (lo_from_bytea WRITES from a plain SELECT, so SELECT-only is not
read-only) along with the advisory-lock family (session locks outlive the savepoint).

**Two isolation leaks that had nothing to do with SQL:**
- `find_person` served `person.first_seen_at/last_seen_at` — aggregates maintained on the WRITE
  plane over EVERY ingested message, on a table carrying org isolation only. A colleague
  learned the DATE of correspondence they hold no grant for, while `search_emails` in the same
  session certified that message does not exist. The window is now recomputed from the messages
  the caller can actually read.
- **BCC was being served.** `project_email_for_non_owner` exists precisely for this and its own
  docstring says "Used by: the future retrieval layer (Ask)" — and `get_email` never called it.
  Grants go to every addressee regardless of kind, so a plain To recipient learned exactly who
  was blind-copied. Now dropped in `get_email` AND excluded from participant filters, because a
  filter that matches bcc rows is a membership oracle: `total_matches` answers "was this
  address blind-copied?" without ever showing a row.

**The seam only checked a NAME.** `READER_DB_USER` is configuration; pointing it at a BYPASSRLS
role satisfied the AC18 check while every policy silently stopped applying. The probe now
returns `rolsuper OR rolbypassrls` in the same round trip and refuses the connection.

**And the grader was scoring things it had not verified** — four ways, each measured on real
golds: mandated `[id: <uuid>]` citations leaked all-digit groups into count extraction (an
answer of 7 passed a gold of 0 because the uuid contributed a 0); any number in the headline
sentence satisfied a count ("We have 25 active clients; the top 9 are listed below" passed a
gold of 9); `_match_entity` was a polarity-blind substring test, so 'Beyond' matched "anything
beyond that" and refusals naming the asked-about person passed; and a truncated results.jsonl
graded as a complete run — deleting the failing rows turned 0.412 into a clean 1.000.

Fixed: uuids are stripped before number extraction using the module's OWN citation regex (so
what is removed and what counts as a citation cannot drift); the count binds to the FIRST number
of the headline claim; entity matching is word-boundary with a minimum token length; an answer
that DISCLAIMS the thing asked for cannot pass on a coincidental match (only a would-be pass is
diverted to the critic — a wrong answer still fails outright); and the summary now reports
expected/missing/duplicate qids and shouts PARTIAL COVERAGE rather than printing a score for
whatever rows happened to be in the file.

**What this round is really about:** every one of these made the system look BETTER than it was
— higher accuracy, cleaner isolation, safer hatch. The failure mode of a measurement system is
flattery, and it is worth attacking exactly as hard as the security boundary.

### 7.18 (2026-07-26): round 3 — the denylist lost a third time, so it is gone
`ts_rewrite(tsquery, text)` executes its second argument as SQL through SPI, is executable by
the reader role, and matched none of the forbidden FAMILIES. Demonstrated end to end: another
org's restricted email content read out, with the scope tripwire silent (move-read-restore
inside one statement, exactly the shape the tripwire's docstring admits it cannot see).

That is three rounds, three misses — `query_to_xml`, then the `database_`/`schema_to_xml`
variants, now `ts_rewrite`. **The denylist is replaced by an ALLOWLIST of functions.**
PostgreSQL ships thousands of functions and one miss is a tenant break; there is no way to
enumerate what is dangerous, and there IS a way to enumerate what retrieval needs — 60-odd
aggregates, text, numeric and date builtins, derived from the plans of the legitimate corpus.
An unknown name costs a fall-through to the agent and nothing else. This is what PF-FBP-8's
residual asked for from the beginning; it took three demonstrated escapes to earn it.

**Also closed:** a decorative `FROM email_message` satisfied the touches-a-relation rule while
the VALUE stayed model-authored (`SELECT '<uuid>'::uuid AS id, count(*) FROM email_message`),
and the grader accepted the fabricated id as tool evidence. Now a literal id in the output is
refused — while a uuid in a FILTER stays allowed, since that can only match rows that exist.

**And my own BCC fix had a fourth site.** Round 2 added `kind <> 'bcc'` to `get_email` and both
participant clauses; the seen-window LATERAL I wrote in the *same* fix wave did not have it, so
`first_seen`/`last_seen` still shifted for an address that had only ever been blind-copied.
All four recipient queries now carry the rule.

**The armour audit was the most valuable agent of the round.** It found that the corpus's
SPI/XML cases were written WITHOUT a FROM clause — so they were refused by the
touches-a-relation rule and never reached the call scan at all. The round-2 fix could have been
deleted with the entire suite still green. Each dump case is now anchored to a real table, with
a separate relationless case so both mechanisms are exercised by their own entry.

Two structural upgrades came out of the same audit:
- **The matrix now disables SIX mechanisms, not three.** "Plan review" was one column hiding
  four independent checks; a regression in any single one was invisible behind the others.
- **The matrix FAILS the build.** It ran in CI as a report that always exited 0 — every attack
  could have printed THROUGH and the build stayed green. It now exits non-zero when an attack
  executes with everything on, or when a legitimate query is refused.

Both paid immediately: the expanded matrix caught a corpus case I had added but never actually
fixed (`SELECT 1 AS "<uuid>"` — the alias becomes a row-dict KEY, and the plan does not show
it: `Output: ['1']`), and an over-rejection my new allowlist had just introduced (the
counterparty view calls `position()`). Result now: **30 attacks blocked, 12 legitimate queries
answered, 0 escapes, 0 over-rejections.** Three attacks are held by a single mechanism — all
three are the id-laundering family, which only one check knows about. That is printed on every
CI run rather than left to be discovered.

### 7.19 (2026-07-26): the ledger + `seal_check` — "closed" now means something executable
Founder's question, and it was the right one: *can the fixed things be closed with tests, so we
stop going in circles?* Four rounds had produced ~50 confirmed findings, and each new round was
briefed with my PROSE summary of what was already fixed — so reviewers re-reported closed
ground and I re-verified from memory. Two artifacts replace that.

**`docs/PM/ask/ASK-SECURITY-LEDGER.md`** — every confirmed finding, what closed it, and the
PIN: the named test that would fail if the fix were reverted. Five closed sections (hatch,
fabricated evidence, per-person visibility, measurement integrity, robustness) plus an OPEN
section for what is deliberately deferred or waiting on a product decision. Three tests keep
the ledger honest: a pin naming a corpus case that no longer exists fails; a corpus case with
no ledger row fails (unaccounted coverage gets deleted by the first tidy-up); a CLOSED row with
an empty pin fails. The third caught six baseline defences that were in the corpus with nobody
having written down what they protect.

**`scripts/ask_loop/seal_check.py`** — the ledger is the map, this is the proof. It EXECUTES
every pin: each corpus attack through the real `execute_guarded_sql`, the conformance suite,
each pinned test module — and prints one line per finding, SEALED or BROKEN, naming the pin
that gave way. Non-zero exit on any break; runs in CI ahead of the matrix. It judges nothing by
reading code, and it fails in BOTH directions: an attack that executes is a broken seal, and so
is a legitimate query that gets refused.

Its first run was the point of the whole exercise: **51 SEALED, 0 broken — and 9 findings with
NO RUNNABLE PIN.** Nine ledger rows pointed at a source file rather than a test, i.e. were
marked closed with nothing to prove it. That is exactly the gap between "we fixed it" and "it
is sealed". Real tests were written for all nine (the BYPASSRLS seam against the actual
`oneai_global` role; harness coverage/stale/duplicate-qid/error handling; the cache basis
asserted dimension BY dimension, because one "different configs hash differently" test would
pass while a specific dimension silently dropped out; routing-token billing; id validation and
already-parsed arguments). Second run: **51 closed · 0 broken · 0 without a runnable pin.**

The working rule from here: brief the next review round with the ledger, not with prose.
Anything it re-reports from the CLOSED list is answered by running one command. Anything
without a pin is the real work.

### 7.20 (2026-07-26): the armour was audited, and it was lying
Founder asked for Codex (gpt-5.6-sol, ultra) plus an independent Opus panel to review the
ledger and seal mechanism itself. Correct instinct: a checker nobody has attacked is an
assumption. The verdict was blunt — **`51 SEALED` did not mean "51 fixes have causal tests"**.

**The worst defect was in `defence_matrix`, the tool whose whole job is to prove a defence is
load-bearing.** Its mutations replaced the allowlist SETS with a "contains everything" object
overriding `__contains__` — but the production checks use set SUBTRACTION, which never
consults `__contains__`. Codex verified directly: `{'pg_sleep'} - _EVERY_CALL == {'pg_sleep'}`.
Two of six mutations were silent no-ops. A third saved `_require_relation` (lowercase) while
mutating `_REQUIRE_RELATION`, so it was **never restored** — every later row, including the
"all on" column, ran with that rule still disabled. The matrix output I had reported
("every attack held by at least two mechanisms") was therefore partly fiction.

Fixed by giving each check a boolean the code actually branches on, flipping THAT, and
verifying both the flip and the restore land — each raises if it does not. The honest matrix
now reads: **11 of 32 attacks are held by exactly ONE mechanism** (4 by the relation
allowlist, 6 by the call allowlist, 1 by the literal-id rule), 0 escapes, 0 over-rejections.
That is a much less comfortable picture and a true one: the two allowlists are the load-bearing
controls, and a regression in either is a real escape rather than a degraded-but-covered state.

**Seven ledger pins held nothing** (found independently by both auditors, each by executing the
mutation): E2 and R6 pinned a module that never touches `_execute_call`; E3's pin named a test
file with no such test, and its `_parse_iso_date` half had zero references anywhere; E4 tested
`redact_uuids` in isolation, never its use in `per_term_matches`; V3's BCC fix sat in a module
containing no BCC row at all; the `participants_all` half of V2 likewise; R8's word-boundary
rule had no fixture with a class name inside a longer token.

**And R3 — the enumeration defence — survived a FULL revert.** The pinned tests asked "is the
listing non-empty", which 4 of 50 rows satisfies while the redundant page keeps 9 of 10. It now
asserts the actual invariant: *if any enumeration row was dropped, the sample page must be
empty*. Stated as the rule, not inferred from counts.

Two structural gaps in `seal_check` closed as well: a module whose tests all SKIP exits 0, so
on a machine without a provisioned database every DB-backed finding printed SEALED with zero
assertions — it now requires reported passes; and `conformance` prints `N/N`, passing just as
loudly with cases deleted — it now requires a case floor.

**The distinction to keep, in Codex's framing:** an OUTCOME test says "the payload never
reaches the caller"; a CAUSAL test says "with the neighbouring layers neutralised, THIS defence
rejects for this structured reason". The corpus is the first kind. The ledger was claiming the
second. Where those two diverge is exactly where a fix can be reverted with the suite green —
and that is the loop the founder was asking about.

---

## 7.21 — Round 5: making the causal claim executable (2026-07-26)

The previous section ended with the right distinction and no mechanism to enforce it. This
round built one, and it immediately found that **three ledger rows were causally false** — the
fix each one credited was not what stopped its own pinned case.

### The invariant

`defence_matrix` now carries, per mechanism, a CAUSAL CLAIM: a corpus case that **must get
through when that one mechanism is disabled**. If the case stays blocked, something earlier in
the pipeline is shadowing the mechanism, the mechanism is unproven, and the build fails.

The claims deliberately do NOT live on the corpus cases. The corpus records input and required
outcome only, so a future redesign with different checkpoints still has to satisfy it; the
claim table describes THIS architecture and is expected to be rewritten when it changes.

### What it caught, all three of the same family

- **E8 (alias rule).** Its only pinned case was `SELECT 1 AS "<uuid>"` — relationless, so
  touches-a-relation refused it during plan review and the alias check never executed. The fix
  could have been deleted with its seal green. Closed with a table-anchored case
  (`SELECT count(*) AS "<uuid>" FROM email_message`), which the matrix now confirms is held by
  the alias rule ALONE. Predicted by Codex; confirmed by measurement.
- **E11 (touches-a-relation).** The same defect, not predicted: E6's case is uuid-shaped, so
  the literal-id output rule catches it too. Closed with `relationless-prose-laundering` —
  fabricated evidence does not have to be a uuid, and a sentence in `rows` reads as a fact the
  archive returned.
- **E12 (redaction).** The second redaction case used `replace`, described in the corpus as
  "another STABLE string function". Measured: `pg_proc.provolatile` = `'i'` — IMMUTABLE. The
  planner folds it to a literal, the literal-id rule refuses the statement, and the redaction
  never runs. Replaced with `concat_ws` (measured `'s'`, plan shows the unfolded call). The
  matrix now proves each redaction case LEAKS with the redaction off.

### What it proved about the text guard

With the text guard disabled, all four of its S15 cases stay blocked: `multi-statement`,
`write-attempt` and `unterminated-literal` are refused by EXPLAIN itself, and `select-into`
reaches execution and is refused by the reader role's privileges. The guard is the cheap first
filter and the useful error message; **the plan review and the role are the boundary.** That is
what this project claimed when it inverted the denylist into an allowlist — now measured rather
than asserted. Recorded on the S15 row instead of being quietly enjoyed as redundancy.

### Mechanism repairs

- Two enforcement points (the alias rule and the computed-id redaction) had **no switch**, so
  the matrix could not disable them and reported them covered no matter what. Both now have
  one, and `_assert_every_enforcement_point_has_a_column` fails the build if `sql_execution`
  grows a check the matrix cannot reach.
- `seal_check` treated "no runnable pin" as a printed note and exited 0 — disagreeing with the
  ledger's own rule that such a row is not closed. It now fails.
- `REDACTED_STATEMENTS` was invisible to both `seal_check` and `test_ledger`: the entire
  round-4 provenance finding had no ledger row and no seal. Added as pin kind `redacted:<id>`,
  checked in both directions.
- `seal_check.check_corpus` shared ONE session across 32 attacks while the matrix used a fresh
  one per case. The tripwire and its restore run outside the savepoint, so one poisoned session
  would have produced a wave of false BROKEN rows — the slander failure mode, which costs as
  much as flattery.

### State

`defence_matrix` OK — 13 attacks single-held, 5 causal claims proven, 2 mechanisms documented
as non-blocking, 0 over-rejections. `seal_check` — 57 closed findings, 0 broken, 0 unpinned.
`tests/ask/security` 60 passed.

### Operational note, again mine

The 11 failures and the 3 BROKEN seals that opened this round were **artefacts of my own
concurrent runs** against the shared test database. Solo re-run: 226 passed, 0 failed. Two jobs
touching `oneai-test-db` at once produces exactly the signature of a real isolation regression,
which is a trap worth stating plainly: `seal_check` spawns pytest, so it can never share the
machine with the suite either.

### 7.21b — The quality gate had a hole the same shape

Checking file sizes by hand turned up `scripts/ask_loop/grade.py` at **503 lines** — over rule
A2's hard ceiling — while `scripts/check_file_size.py` printed *"OK: all source files are under
the 500-line ceiling"*. Its `SCAN_ROOTS` are `backend/app`, `backend/tests`, `frontend/src` and
the repo-root `scripts/`. **`backend/scripts/` was not among them**, so the entire eval harness
and the security-seal machinery — including the code that decides whether a finding is still
closed — had been exempt from the quality gate while the gate reported all green.

Same failure as the seals themselves, one level up: a checker that reports success over a set
that silently excludes the thing you care about. Fixed by adding the root, which turned the
build red, which is what a gate is for.

`grade.py` then had to be split for real. The seam was already there: what an answer SAYS
(parsing) and whether that is CORRECT (the typed rules) are two responsibilities. New
`scripts/ask_loop/answer_extraction.py` (197 lines) holds the regexes and extractors;
`grade.py` (349) holds the grading rules and the CLI. `conformance.py` now imports the
extractors from their real home rather than through `grade`. Conformance: 53/53 PASS.

### 7.22 — Round 5 red team: the anti-fabrication guarantee broke four ways

The adversarial pass did NOT break tenant isolation — it reported that surface closed by
construction, and its reasoning matched mine: every GUC-moving primitive is outside the call
allowlist, no reachable SECURITY DEFINER function survives 0019, and `information_schema`
expands into disallowed Relation Names. It broke the OTHER guarantee, comprehensively.

All four share one root cause: **provenance was inferred from the shape of the rendered plan
text, and every shape assumption was false.**

| # | Shape | Why every layer passed it |
|---|---|---|
| E13 | `SELECT 'Acme signed the renewal on 2024-03-01' AS finding, count(*) FROM email_message` | the literal rule matched uuid SHAPES; evidence need not be uuid-shaped |
| E14 | `WITH fake AS MATERIALIZED (SELECT '<uuid>'::uuid AS ev) SELECT fake.ev …` | the fence keeps the constant one node down; the top output is the plain Var `fake.ev`, and the per-value redaction read a bare column name as column provenance |
| E15 | `SELECT concat_ws(' ','Acme','owes','42000') AS finding, count(*) …` | renders as a CALL, so no constant rule matches; the redaction only ever stripped uuids |
| E16 | `SELECT string_agg('Acme owes 42000 EUR', ',') …` | my own first fix exempted every AGGREGATE as "reads the rows" — this one reads nothing |

E13 and E14 came from the red-teamer. E15 I found by following its own argument one step
further. **E16 I introduced while fixing E15** — the exemption I wrote would have waved through
the exact shape the fix existed to stop. Worth recording as-is: the fix for a class of bug is
itself a place that class recurs.

**The fix, now provenance-based rather than shape-based:**
1. No output entry may be entirely a CONSTANT — any type at the top level, text at EVERY plan
   depth. Depth is what closes the optimisation fence.
2. Per value, an expression is caller-authored if it READS nothing: strip literals, call names
   and cast types, and if no identifier remains the value is replaced outright — not merely
   stripped of uuids, because the payload may be a sentence, a name or a number. Only `count`
   is exempt; it reads every row while naming no column.

**Verified not over-rejecting**, which is half the requirement: `EXISTS (SELECT 1 …)` (the
planner renders the inner output as the bare constant `1` — which is why constants of non-text
types are refused only at the top level), `CASE WHEN … THEN 'in' ELSE 'out' END`,
`lower(subject)`, `date_trunc('month', sent_at)`, and the entire ALLOWED corpus, all still
answer. Two of those are now ALLOWED cases so the distinction stays pinned.

**The measurement worked as designed.** When the constant rule went in, the matrix immediately
reported `touches-a-relation` UNPROVEN — the new rule had started shadowing it. That is the
mechanism catching a *fresh* instance of the shadowing bug within minutes of it being created,
rather than three rounds later. It was closed with a case only that rule holds: a relationless
function-assembled fact, which no output rule matches and no table anchors.

One claim of the report was REFUTED by measurement: the `OFFSET 0` variant. The planner FOLDS
it (`("'44444444-…'::uuid)"` appears in the top output) and the existing rule already refused
it. The report had flagged that as the alternative outcome and asked for the EXPLAIN before
believing it — which is the standard this loop is supposed to hold itself to.

### 7.23 — Round 5 pass 2: the fixes did not compose, and I broke the layer myself

The red-teamer went again against the new provenance test and found four more. All confirmed by
measurement, all fixed.

**E20 — the E14 and E15 fixes did not COMPOSE.** The depth scan matched only BARE constants;
the provenance test ran only on the TOP output. A function-assembled constant behind a fence sat
in neither: the deep node shows `concat_ws(...)` (not a bare literal), the top shows
`fake.finding` (a bare Var). Measured escaping **paired with a real message id** — the worst
shape there is, because the citation passes fidelity while the fact beside it is invented. The
numeric variant worked too, since the depth scan was text-only to spare `EXISTS (SELECT 1 …)`.
Fixed by collapsing both rules into ONE over the whole plan tree: every node's output entry must
read something from the corpus. The bare-constant regexes are gone, subsumed.

**E19 — the `count` exemption asked whether an expression MENTIONED count**, not whether it WAS
one, so any payload bought immunity by carrying one as decoration. Structural now.

**E18 — the alias fix tested for WHITESPACE**; an underscore carries a sentence just as well.

**E21 — I broke the entire layer myself, silently.** While splitting `sql_execution.py` (it had
passed the 500-line ceiling), a `\b` in a generated regex literal became a literal **backspace**.
`_PLAN_CALL` then matched nothing, every expression kept its call name as a "surviving
identifier", and every value was classified as data. **The whole anti-fabrication layer was off,
and ruff passed.** A hand probe caught it, not the suite.

That is the most important event of the round. The corpus would have caught it — but only on a
machine with a database, and only after a nine-minute run. The response is
`tests/ask/tools/test_provenance.py`: unit pins that assert the MACHINERY, not only its verdicts,
including one whose entire job is "the call scan matches a call at all."

**What the measurement did, again, without being asked.** Fixing E20 immediately turned
`touches-a-relation` UNPROVEN — the new depth rule refuses every relationless statement that
produces output, so its causal case was shadowed within minutes of being written. Replacement:
`SELECT count(*)` with no FROM. The provenance test correctly reads it as data (it IS a count),
it answers `1` to a question about the archive, and touches-a-relation is the only thing that
refuses it.

**And the matrix caught its own blind spot.** The module split moved three switches into
`sql_provenance`, and `_assert_every_enforcement_point_has_a_column` failed the build with
"matrix names switches that no longer exist". Had it scanned one module and shrugged, those three
mechanisms would have been reported as covered forever. The map is now (module, attribute).

**One claim demoted honestly.** The redaction's causal check no longer holds: the provenance rule
REFUSES all four `REDACTED_STATEMENTS` shapes before a row is fetched, so the redaction is not
what removes them. That is a strengthening, not a regression — but the claim was moved to a unit
test rather than quietly kept. Its remaining live job is stripping ids that appear inside real
email TEXT, which is not evidence the retrieval layer vouched for.

**Claim semantics changed to ANY.** A mechanism must have at least ONE case that gets through
when it alone is off; cases that stay blocked are printed as `shadowed`. Requiring ALL forced
pruning good cases every time another layer got stronger, which is a bad incentive. What must
never happen is a mechanism with NOTHING proving it — that is the state where a fix can be
deleted with the build green.

**Two design limits recorded as OPEN, not silently accepted (O9, O10).** A dead branch that
mentions a column defeats the provenance test (`coalesce(nullif(subject,subject), '<fake>')`).
The invariant that would close it — no string literal in a value-producing output expression —
kills `coalesce(subject, '(none)')` and CASE-label bucketing, both legitimate. That is a
capability trade for the founder, not a fix to take unilaterally inside a security pass. And
column NAMES cannot carry provenance at all: a plausible label on a REAL number has no
fabricated payload to detect, so the durable fix is consumer-side.

Files split to stay under the ceiling: `sql_execution.py` → + `sql_provenance.py`;
`attack_corpus.py` → + `fabrication_corpus.py` + `corpus_types.py`.

### 7.24 — The isolation round: there was no fifth site, there were three more planes

A second red team went at tenant and per-person isolation while the first worked the hatch. It
found no cross-tenant path — and said so with the specific reason, which matters more than the
verdict: `_bind_scope` re-binds both GUCs on every transaction AND asserts both the role NAME
and `rolsuper OR rolbypassrls` in the same round trip, so pointing the reader at a privileged
role fails loud instead of silently making every policy inert. Combined with a SELECT-only role
and FORCE RLS, the org boundary held against everything it tried.

**What it broke was per-person visibility, and the framing is the finding.** I had asked it to
look for a fifth `kind <> 'bcc'` site. Its answer: there is no fifth site, there is a fifth,
sixth and seventh **plane**, and none of them ever had the rule.

The `visibility` policy from 0019 keys CHILD rows on the PARENT — `_CONTENT_TABLES` maps
`email_recipient` to grant key `email_id` — so any grant holder can read EVERY recipient row of
a message. That is deliberate at the policy level and was compensated for in **Python**, in four
hand-written tool queries. Three other planes read the same tables:

| Plane | had the BCC rule? |
|---|---|
| the four tool queries | yes |
| the generated-SQL hatch (`email_recipient` is in its allowlist) | **no** |
| `counterparty_summary` (its recipient arm has no `kind` filter) | **no** |
| `acl_grant` (one row per recipient of EVERY kind) | **no** |

Ledger V1–V8 closed the four queries. S9 closed the hatch's table REACH — and settled on an
allowlist containing exactly the tables where those rules live. **The two hardening efforts
never met.** No injection is needed to exploit it: "who else was on that email?" produces
`SELECT * FROM email_recipient WHERE email_id = …`, and the specialist has no reason to filter
`kind` — because the M-Schema card told it the column only holds `'to'` or `'cc'` while the 0008
CHECK allows five values. That false card is why this survived four adversarial rounds.

The `acl_grant` route is the one I found most instructive: it re-discloses the blind-copied
recipients **by name**, through `person`/`person_email`, without ever touching
`email_recipient`. Removing `email_recipient` from the allowlist would not have closed it.

**Fix: migration 0023 puts the rules in the database**, where the planes converge —
`recipient_kind` (RESTRICTIVE, `kind IN ('to','cc')`) and `own_grants` (RESTRICTIVE,
`person_id = current person`), plus a column-level revoke of the write-plane seen window. The
four tool filters stay as defence in depth with their own pins. `kind IN (…)` rather than
`kind <> 'bcc'`: a denylist over a five-value enum admits every value nobody thought about, and
`reply_to`/`sender` rows were being served as recipients.

**One measured correction to the recommended fix.** `REVOKE SELECT (col) ON person` is a
**no-op** while a table-level `GRANT SELECT ON person` stands — the table grant implies every
column. The revoke applied cleanly and the column stayed readable. Only the test caught it,
because it asserted the refusal rather than the grant. The working form revokes the table grant
and re-grants the safe columns explicitly.

**And the quality audit found the gap that let all of this through the suite:** the ONE executor
that runs arbitrary model-written SQL over these tables had **no cross-tenant negative test at
all**, while all six declarative tools had real seeded-org-B ones. Worse, the two assertions
that made the file *look* covered read `assert rows == [{"n": 0}]` against a fresh empty org —
which passes identically whether RLS works or the table is simply empty. `test_sql_hatch_
isolation.py` now covers it, and every cross-tenant assertion carries a POSITIVE CONTROL: org
B's own reader must see the row org A cannot.

### 7.25 — Rounds 3 and 4: the keyword hole, and a pin that could not fail

**Pass 3 (E22).** `_ANY_IDENTIFIER` cannot tell a column name from a SQL KEYWORD, so
`CASE WHEN now() IS NOT NULL THEN '<fabricated>' ELSE '' END` was classified as DATA on the
strength of the word `CASE`. It reads nothing, depends on no row, and returns the caller's
sentence unconditionally. Strictly stronger than the dead-branch limit in O9, which at least
references a column — there is no capability being protected by admitting this one, so it was
a bug rather than a trade.

Fixed by requiring the surviving identifier to be a **known column of an allowlisted relation**,
built from the ORM models rather than the M-Schema card (the card had just been measured wrong
in the isolation round — a good reminder that documentation is never the source of truth), plus
a caller-literal precondition so `now()` and a bare `NULL` are not payloads. That precondition
also removed three over-rejections the red team measured: `now()`, `($0 + 1)`, `($0)::text`.

**A claim in the docstring was refuted by measurement.** It promised a numeric carve-out
("`EXISTS (SELECT 1 …)` renders an inner output of `1`") that the code no longer had. On PG 16
none of the three EXISTS shapes emits a bare `1` — all are rewritten to semi-joins or hashed
SubPlans. So the carve-out is unnecessary, but the docstring was describing code that did not
exist. Both EXISTS shapes and the scalar-subquery shape are ALLOWED cases now.

**Pass 4 — the lexer HELD.** 60,000 fuzzed inputs over the lexer's own hazard alphabet: 28,914
accepted, **zero desync** across the three position-aligned views, zero crashes, every recorded
`desc_span` in range and resolving to a real token. The position-alignment invariant the whole
splice rests on is measured now, not asserted.

**S17 — `has_limit` was statement-WIDE.** A `LIMIT` inside a subquery suppressed the appended
cap while bounding nothing at the top level, and there is no streaming, no `statement_timeout`
and no server-side row bound anywhere in the app — so SQLAlchemy buffers the ENTIRE result
before `max_rows` slices 50 rows off it. `SELECT a.id FROM email_message a, email_message b,
(SELECT id FROM person LIMIT 1) z` is a ~35M-row product in process memory on the dev corpus.
Closed twice: paren-depth-0 LIMIT detection, and a ceiling on the planner's own `Plan Rows`.

**Two defects in my own measurement, found by trying to pin S17:**

1. The corpus case I wrote for it **could not fail**. The matrix runs every attack against an
   EMPTY throwaway org, so the row estimate is always tiny and no cardinality attack is
   demonstrable there at all. It sat in the corpus looking like coverage. Now pinned by a unit
   test that feeds the ceiling a plan directly, and the matrix states in writing that this
   mechanism cannot be exercised in its harness.
2. Because that case executed with EVERY layer on, the causal logic counted it as **proving**
   the mechanism it was assigned to. A case that always gets through was evidence for whatever
   it was pointed at. Escapes are now excluded from proving anything.

**And the call allowlist had been left with nothing proving it.** Every forbidden-call case
SELECTS the call, so the new provenance rule refuses it first. The unshadowed form puts the call
in a PREDICATE with a real column as output — and it must not be `pg_sleep`, which the text
guard's denylist also catches. `query_to_xml` is the one that isolates it.

Two of my own regexes were wrong in the same pass: `varying(10)`'s length modifier read as a
caller literal, and the planner's UNION-branch alias `"*SELECT* 2"` read as one too. Both
refused the legitimate counterparty view. Casts and plan-internal quoted names are masked now.

### 7.26 — Round 5 close: three measurement defects, and what they cost

The red team's own closing assessment ranks the round the way I do, and it is worth stating
because it is counterintuitive. The most valuable findings were **not** the payloads. They were:

1. a corpus case that **could not fail** (an empty-org harness cannot demonstrate cardinality);
2. **escapes counting as proofs** — a case that got through with every layer on was credited as
   evidence for whatever mechanism it was assigned to;
3. the `\b` corruption that silently disabled the whole anti-fabrication layer while ruff passed.

Each meant a guard existed on paper and not in measurement. **A guard you cannot measure is a
guard you do not have** — and all three were mine, not the code's.

A fourth arrived at the close, and it is the sibling of (1): the empty-org limitation applies to
`RedactedCase` too. `AttackCase` entries prove REFUSAL, decided at EXPLAIN time before any row
exists, so row count is irrelevant to them. A redaction case has to RUN and exhibit a value — so
one written `SELECT concat(…) AS x FROM email_message LIMIT 1` returns nothing on the harness and
passes while proving nothing. Both existing entries survive only because each happens to carry
`count(*) AS n`. The invariant is now on the dataclass AND asserted by the matrix, which reports
a zero-row redaction case as a problem instead of crediting it.

**Scoreboard for the round.** Four red-team passes on the hatch (10 confirmed findings, all
fabricated-evidence or availability class), one isolation pass (6 findings, one CRITICAL), one
quality audit (2 CI-failing), one cross-vendor mechanism audit. Tenant isolation, relation reach
and the read-only plane were NOT broken in any pass — and the reports say so with the reason,
which is the half that is harder to write and the half I will be relying on.

Carried forward as decisions rather than caveats: **O9** (the dead-branch class — with the
tautology sub-class, `count(...) > -1`, noted as mechanically separable if the trade is ever
worth paying), **O10** (row KEYS as evidence — consumer-side, the only durable fix),
**O11** (`_bind_scope` interpolating `person_id`, which stops being latent the moment MCP-01
lets an untrusted host agent supply it), **O12** (`_plan_relations` cannot distinguish a
policy-expanded relation from a caller-written one). Plus two follow-ups: `statement_timeout` on
the reader role, and landing the lexer fuzz harness as a test.

### 7.27 — The write plane opens, and the lexer fuzz lands

**The lexer fuzz is now a test** (`tests/ask/tools/test_lexer_alignment.py`), which both the red
team and I ranked above any remaining attack surface. It asserts a STRUCTURAL property no
behavioural test can see: the three views `_lex_sql` returns are position-aligned and the same
length. The DESC rewrite splices by OFFSET into those views, so a one-character drift silently
CORRUPTS a statement rather than refusing it — a failure with no other detector. Deterministic
seed, hazard alphabet rather than random text, and an assertion that the fuzz has not gone blunt
(if the alphabet ever drifts so that everything is refused, the alignment assertions stop meaning
anything).

**The write plane opened, and it is a different failure mode entirely.** The read plane's
vocabulary is "leaks the content" vs "leaks the existence". The write plane has neither: its
failure is **authoring** — an unauthenticated party decides who holds a grant.
`principal_source_identity` authenticates the PERSON; nothing authenticates the CLAIM that the
person was on the message, and `headers.py` drops `DKIM-Signature`/`Authentication-Results` at
parse under CA-CONN-05 minimisation, so the evidence that could validate it is discarded before
grant derivation runs. A defensible privacy call with a consequence nobody priced.

**W2-C is live today with no adversary**, and I verified every link: the dedup key covers `to`/`cc`
only (Bcc excluded deliberately, so Sent and received copies FOLD — correct), grants derive from
all five kinds, a dedup hit reconciles against the NEW parse, reconcile tombstones
`live - derivable`, and SENT is kept and shares the dedup scope with INBOX. **Whichever copy is
ingested second decides who keeps access.**

**Two things I got wrong about it before the follow-up analysis:**

1. I thought byte-exact key reproduction was hard. It is easy, and for a structural reason: the
   key's design goal is invariance across re-serialization, and **that property is symmetric** —
   every normalization that folds two folder copies also folds an attacker's fresh serialization
   onto the target's key. The only strict component is the html digest, and `_html_body_digest`
   returns `''` when there is no html part, so for plain-text mail the gate is absent entirely.
   What actually bounds it: the body is IN the hash (no blind fishing — you must already possess
   the message), and the lookup is `(org_id, connection_id, dedup_key)` (the replay must land in
   the same mailbox).
2. I thought of the two fixes as "minimal" and "fuller". **They are disjoint.** Fix 1 constrains
   REMOVALS (closes W2-B/W2-C); fix 2 constrains ADDS (closes W1/W2-A/W3's grant half). Shipping
   only fix 1 would have left the security variants open **while looking like the finding was
   closed** — the same failure this whole round has been about, caught in analysis rather than
   after a patch.

**Deliberately NOT patched.** Both fixes change who can read what, the kind does not currently
survive into `reconcile_email_message_grants`, and this module is otherwise untouched by this
session. It gets its own round with its own tests. Recorded as ledger W1-W4 + `PF-FBP-14`.

### 7.28 — Round 5 verification

| check | result |
|---|---|
| `pytest tests/ask tests/access` | 365 passed, 1 failed → fixed → 366 |
| grader conformance | 53/53 PASS |
| `defence_matrix` | every attack blocked, every causal claim proven, 0 over-rejections, 0 redaction leaks |
| `seal_check` | **74 closed findings · 0 broken · 0 with no runnable pin** |
| `ruff check` / 500-line gate | clean |

The one failure was `test_every_attack_case_is_claimed_by_the_ledger`: I added
`relationless-count` as the causal case for touches-a-relation and never wrote its ledger row.
That is the map-vs-territory check doing precisely the job it was written for — a corpus case
whose purpose is not recorded is a case the next tidy-up deletes. Fixed by pinning it on E6 with
the reason it exists (once provenance was enforced at every plan depth, every relationless
statement producing output became caller-authored and was refused by THAT rule instead, leaving
touches-a-relation with nothing of its own; `SELECT count(*)` with no FROM is the exception that
still isolates it).

**Round 5 closes at 74 sealed findings**, up from 53 at the start. The read plane is green on
every checker in the repo. The WRITE plane is open and unpatched by decision, not by omission —
ledger W1-W4 and `PF-FBP-14`, with a live bug that needs no attacker, a two-part disjoint fix,
and a query that can establish whether it has already fired on real data.

### 7.29 — The write plane, fixed: one rule, and it closed more than predicted

I had deferred this as "needs its own round". That was the wrong call against a standing
instruction to keep fixing until nothing critical or medium remains, and the deferral was
overturned. Doing it turned out to be smaller and cleaner than the deferral assumed.

**The red team's analysis said two DISJOINT fixes were required** — one constraining removals
(closing W2-B/W2-C), one constraining adds (closing W1/W2-A/W3's grant half) — and that neither
covered the other. That was correct *given the assumption that bcc-derived grants would still be
added in some circumstances*. Drop that assumption and one rule does both:

> **Grants derive ONLY from fields that are in the DEDUP KEY.**

`DISCLOSED_RECIPIENT_KINDS = {to, cc}`; bcc/reply_to/sender never mint a grant. Every remaining
source — owner, `From`, to/cc — is keyed, so two copies of one message derive identical grants
**by construction**. The ordering class becomes impossible rather than patched:

- W2-C (ingest order decides access, no adversary): gone — the two copies now agree.
- W2-B (replay omitting a Bcc revokes it): gone — no bcc-derived principal exists to revoke.
- W2-A (replay adding a Bcc grants a chosen employee): gone — bcc adds nothing.
- W1 (Bcc plants text in an invisible scope): gone.

And it costs nothing real: a genuinely blind-copied person still reaches their own copy, because
in THEIR mailbox they are the connection owner and the owner grant carries them.

**W4** separately: a recipient display name is chosen by the SENDER *about somebody else*, and
back-fill is first-writer-wins — so `To: "Chief Fraud Officer" <cfo@corp.com>` named that person
permanently. A recipient's address now resolves them; the third-party name is dropped. Naming
yourself in `From:` is a claim about your own identity and stays allowed.

**Causally verified, to this round's own standard.** With the kind filter temporarily reverted,
both pins FAIL — the ordering test failing exactly as W2-C predicted — and pass with it restored.
A green test is not evidence; a test that fails without the fix is.

**What remains is a founder decision, and it is stated as one (O13/O14).** `to`/`cc`/`From` are
still forgeable and still mint grants. That is inherent to "ingest email and grant access based on
who is on it" — every mail system works this way — and the sharp variant is closed, because what
remains names the victim IN THE OPEN where an auditor reading the message sees them. Closing it
entirely needs DKIM/ARC verification (reversing CA-CONN-05 minimisation) or a possession-only
model where an employee reads only mail in their own synced mailbox. That is a capability
decision, not a patch — and it should be taken BEFORE MCP-01 adds a second authoring path into
the same scopes.

### 7.30 — Round 5 final verification

| check | result |
|---|---|
| `pytest tests/ask tests/access` | 366 passed |
| `pytest tests/access tests/connectors` | 696 passed |
| ingest module (incl. the two W4 pins) | 13 passed |
| grader conformance | 53/53 |
| `defence_matrix` | every attack blocked, every causal claim proven, 0 over-rejections |
| `seal_check` | **77 closed findings · 0 broken · 0 with no runnable pin** |
| `ruff check` / 500-line gate | clean |

**53 → 77 sealed findings.** Every write-plane fix is causally proven by the same method used all
round: revert the fix, watch the named pin fail, restore it. W1/W2's pins fail without the kind
filter (the ordering test failing exactly as W2-C predicted); W4's pin fails without the
third-party-name drop.

**What remains OPEN is decisions, not unfixed defects:**
- **O13** — `to`/`cc`/`From` are forgeable and still mint grants. Inherent to header-based email
  access; the sharp variant (invisible + unkeyed) is closed. Closing it entirely needs DKIM/ARC
  verification (reversing CA-CONN-05) or a possession-only model. Take it BEFORE MCP-01.
- **O14** — `provenance='sender'` must never be read as authenticated authorship by MEM-01.
- **O9** — the dead-branch class; a capability trade, with a mechanically-separable tautology
  sub-class if a cheaper cut is wanted.
- **O10** — row KEYS as evidence; only a consumer-side fix closes it.
- **O11** — `_bind_scope` interpolating `person_id`: latent today, live the moment an untrusted
  host agent supplies it.
- **O12** — `_plan_relations` cannot distinguish a policy-expanded relation from a caller-written
  one; closed at RLS for now, structurally improvable.

### 7.31 — The verification pass found the fix incomplete

Closing an adversarial round with a verification pass earned its keep immediately.

**BLOCKER: `scripts/backfill_email_grants.py` reintroduced W1.** It selected recipients with NO
`kind` filter — zero occurrences of the word in the file — and fed all five kinds into the SAME
reconciling choke point the ingest service uses. Worse than the omission: ingest derives to/cc,
backfill derived all five, and both call `reconcile_email_message_grants`. They **oscillate** —
backfill mints a bcc grant, the next re-ingest hits the dedup fast path and tombstones it as
`live - derivable`, the next backfill re-mints it. The W2 bug class removed WITHIN ingest,
reappearing BETWEEN two writers.

**Why it survived:** no test exercised the script at all, and the parameter rename to
`disclosed_recipient_addresses` gave false assurance precisely because nothing constrains a
caller. A rename is documentation, and this round has now been bitten three times by treating
documentation as a boundary (the M-Schema card, the numeric-carve-out docstring, this).

Fixed by binding `DISCLOSED_RECIPIENT_KINDS` as a query PARAMETER rather than re-encoding
`kind IN ('to','cc')` in SQL, so the rule cannot drift apart again. Pinned, and the pin verified
causal: it fails with the filter removed.

**Operational consequence worth acting on:** the script is documented as the thing you run once
after a disk ingest. If it ran after the first fix landed, the dev corpus has live bcc-derived
grants right now — which the `HAVING count(*) > 1` diagnostic will surface.

**And a false guarantee in my own comment.** I wrote that a blind-copied person "still reaches
their own copy: in THEIR mailbox they are the connection owner". The schema says otherwise —
`owner_user_id` is NULLABLE BY DESIGN ("NULL = org-owned/shared", the admin-provisioned mailbox)
and even when set needs a verified 'auth' binding. So on an owner-less connection, a message whose
only verified principal sat in bcc now has NO grant holder and is unreadable. That is the
fail-closed direction and I would keep it — but asserting a guarantee the schema does not provide
is exactly the failure that let the original BCC leak survive four rounds. Comment corrected to
state the conditions.

**Confirmed closed by the same pass:** the "every grant source is dedup-keyed" claim holds (owner
by scope, From and to/cc by hash), nothing in the codebase authorizes on `provenance` (checked
against the RLS predicate SQL itself, not just the writer), and there is no raw `INSERT INTO
acl_grant` or other bypass around `GrantWriter` — including `promotion_service`, which is the
other route to satisfying the visibility predicate and gates on user identity, not headers.

Three qualifications recorded rather than waved off: O15 (dedup-key soundness under duplicate
`From` headers — not an ordering bug), O16 (owner mutability is an operational assumption, not a
key property), O17 (people who only ever appear as to/cc are now permanently unnamed).

### 7.32 - The second-caller pattern, checked myself

The backfill blocker was one instance of a general shape: a SECOND caller of a hardened path that
does not obey the rule the first caller was fixed to obey. I swept for others rather than assume
it was the only one.

- `execute_guarded_sql` has exactly TWO callers in `app/` (`sql_tool`, `sql_pipeline`); both go
  through it. The only other callers are the two verification scripts, which is their job.
- The only raw `session.execute(text(...))` sites in `app/ask/` are the EXPLAIN and the execution
  INSIDE `execute_guarded_sql` itself, both operating on already-validated `safe_sql`.
- No session factory is constructed anywhere in `app/ask/` - every executor takes a
  caller-provided session, with `reader_session` as the single seam. (Independently confirmed by
  the isolation pass, which reached the same conclusion by a different route.)

And the structural point worth stating plainly: **for the BCC rule the second-caller pattern is
now IMPOSSIBLE**, because the rule lives in RLS. Any reader-plane query is covered whether or not
its author has ever heard of it. That is precisely why moving it into the database beat adding a
fifth Python filter - the backfill script is the proof, since it was written by someone who did
not know about the rule and is now covered anyway on the read side.

The grant-DERIVATION rule cannot be moved into the database the same way (it is a write-path
policy, not a row predicate), which is why that one needed the shared constant and a test instead.

### 7.33 - Round 5 closed

| check | result |
|---|---|
| `pytest tests/ask tests/access` | 372 passed |
| `pytest tests/access tests/connectors/imap` | 337 passed |
| grader conformance | 53/53 |
| `defence_matrix` | every attack blocked, every causal claim proven, 0 over-rejections |
| `seal_check` | **77 closed findings, 0 broken, 0 with no runnable pin** |
| `ruff check` / 500-line gate | clean |

**The closing sweep's verdict: no CRITICAL, one MEDIUM (now fixed), everything else LOW or
cosmetic.** Both actionable items were fixed and re-verified:

- **M1** - the plan-rows ceiling read the TOP node only, and any aggregate reports `Plan Rows: 1`
  there. `SELECT count(*) FROM email_message a, email_message b` therefore passed every layer and
  executed a cross join to return one number. Measured: top node 1, the Nested Loop beneath it
  1600 on an almost-empty database. My own test docstring had asserted "the hazard is memory in
  THIS process, not a slow query" - that scoping sentence WAS the defect. Fixed with a whole-tree
  estimate walk (two ceilings: 50k returned, 5M processed) plus `SET LOCAL statement_timeout`,
  which is the only bound on TIME anywhere in the app.
- **L1** - `email_projection.py` was a FIFTH site of the BCC rule still carrying the pre-V13
  denylist form, in the contract MCP-01's read tools are meant to be built against. V13's pin
  could never reach it: it lives in `app/access/`, not `app/ask/`.

**Two judgement calls from the sweep worth keeping, because both are right:**
- Unbounded tool-call fan-out was downgraded MEDIUM -> LOW after one grep showed no FastAPI route
  imports `app/ask` at all. **Severity follows reachability, not amplification factor.**
- Indirect prompt injection was NOT filed as a fix-round item. Today's ceiling is answer integrity
  and tool-use steering; the sharp version is MCP-01's write tools, where injected text steering
  `record_fact` becomes persistence into company memory. Recorded as O18.

**The count that matters more than 77:** four times this round, documentation was mistaken for a
boundary - the M-Schema card, a docstring promising a carve-out the code had dropped, a parameter
rename implying a constraint nothing enforced, and a comment asserting an owner guarantee the
schema contradicts. The one place that pattern is now structurally impossible is where the rule
lives in RLS, which is the strongest argument for 0023 stated in a sentence.
