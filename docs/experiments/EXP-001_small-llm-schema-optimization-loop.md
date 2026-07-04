---
id: EXP-001
title: The schema-optimization loop — evolve the derived layer until a small LLM maxes out
date_started: 2026-07-04
date_concluded:
status: paused           # DESIGNED, NOT STARTED — awaiting Yani's review/sign-off of this design
tags: [retrieval, models, schema, loop, eval]
related: []              # consumes docs/small-llm-first-foundation-plan.md P0.4 + study §6(k)
---

# EXP-001 — The schema-optimization loop

> **Design memo only (2026-07-04, night shift).** Nothing is built, no DB copy exists, no loop
> runs. Method sources: the forwardfuture loop library (bounded agent loops: goal → one mutation
> per cycle → explicit checks → stopping conditions → regression-revert → holdout validation),
> today's foundation plan + panel study, and the model-landscape checks cited in §4.6.
>
> **REVISED same night after a Codex/GPT cross-vendor gap review (16 findings, 6 blockers —
> §4.12 records every verdict). Consequence: the first run is a PILOT, not a confirmatory
> experiment** — the ≥85% target is an exploratory point target with confidence intervals until
> the sealed-test protocol and measured runtime budget exist. Where §4.12 conflicts with earlier
> sections, §4.12 wins (kept inline rather than rewriting history).

## 1. Question

Starting from a transaction-consistent logical copy of the current Connect DB, can an automated
**mutate → evaluate → select** loop over the *derived* layer (projections, tools, indexes,
payload shapes — never the raw layer) raise a small LLM agent's score on a gold question set to
a defined target — and which mutations buy the most per euro?

## 2. Hypothesis

Written before running, so we can be honestly wrong:

1. **Baseline will be dismal** (<10% strict): a small model + raw normalized schema + free SQL is
   the v2 configuration that measured 0–7%.
2. **The deterministic spine recovers most of the gap**: identity merge, thread materialization,
   language, typed tools with answer packets — expected to be the four biggest single jumps
   (external evidence: +17–23pp from explicit semantics alone; structure-first reframes make
   3B/7B usable).
3. **The loop finds non-obvious wins humans skip**: tool descriptions/payload wording, result
   ordering, candidate-count defaults, index choices — cheap mutations with outsized effect on
   weak readers.
4. **A plateau exists** and it is ABOVE the usefulness bar for the sovereign 7–8B target
   (if this is false, the sovereign-reader strategy needs rethinking — that is worth knowing NOW).
5. **Good schema shows up as FEWER agent turns**: median `turns_to_answer` falls as derived-layer
   mutations land (the one-hop thesis made measurable).
6. **Entrance-LLM refinement (Stage E) adds a second, smaller jump on top of the deterministic
   plateau** — the ingest-time-refinement reframe from v2, finally measured instead of assumed.

## 3. Why it matters

- It converts the foundation plan's remaining design debates (tools vs views, prefix vs columns,
  dossier shapes) into **measurements on our corpus** instead of opinions.
- It validates (or falsifies) the strategic bet that a **sovereign on-prem 7–8B reader** can win
  on a properly-shaped DB — before we build the Ask layer on that assumption.
- Winning mutations are not throwaway: each is a migration + tool change that can be promoted
  into the real build with its measured lift attached.
- Its Stage A **is** the foundation plan's P0.4 (eval harness + answer contracts) — one artifact,
  two consumers; no duplicated work.

## 4. Setup / Method

### 4.1 Sandbox (hard isolation — the live dev DB is never touched)

- `pg_dump` of the dev DB → restore into a **separate container + volume** (`oneai-lab-db`,
  host port **55433**; 55432 is live-dev, 5432 belongs to another project). The copy carries the
  full verified corpus (5,893 emails / 8,454 attachments / entity graph).
- Lab code lives in a **git worktree** off `main`; every accepted iteration is one commit
  (mutation script + config + scores) — checkpointed, resumable, post-mortem-able.
- A `reset.sh` (re-restore from the dump) makes any iteration reproducible from zero.

### 4.2 The loop (one bounded mutation per cycle)

```
propose ONE mutation (operator: Claude session, from the pre-registered backlog §4.7 or its own analysis)
  → apply as a migration script to the LAB db (idempotent, revertible)
  → run the eval harness: small-model agent answers the DEV question set via the tool layer
  → score (metric vector §4.4; N=3 repeats, temperature 0)
  → ACCEPT iff composite improves AND no hard gate regresses  → commit + ledger row
    → INDEPENDENT VERIFIER (§4.8, isolated context): re-scores + audits the diff for
      question-specific gaming — GAMED verdict reverts even a score-improving mutation
    else REVERT (restore/down-migration)                      → ledger row with failure reason
  → repeat until a stopping condition (§4.5)
Periodically (every ~5 accepted mutations): HOLDOUT run + checkpoint-level verifier pass —
divergence or a failed verification = stop and prune.
```

Ledger: `EXP-001_ledger.md` — one row per iteration: mutation, hypothesis, diff ref, dev score
vector, cost, verdict, note. The ledger is the experiment's §5 Results.

### 4.3 Mutation space

| MUTABLE (the derived/tool plane) | FROZEN (invariants) |
|---|---|
| Projection tables (threads, edges, dossiers, rollups), views, indexes (btree/GIN/trgm/partial), generated columns | Raw-layer semantics (email/attachment/entity source rows) |
| Typed tools: set, signatures, payload shapes, descriptions, candidate counts, error/empty vocabulary | Tenancy: org_id + RLS on everything new; erasure hooks wired |
| Tool-schema serialization shown to the model; system prompt of the agent | The eval questions + graders (change = new experiment) |
| FTS/trgm configs; deterministic enrichment columns (language, addr-spec fixes, dedup pointers) | The reader model + params within a stage (change = new arm, §4.6) |
| Guarded SQL hatch on/off + its schema subset | — |
| Agentic scaffold of the reader (§4.4a): turn cap, repair-on-error, reflection on/off | — |
| **Model parameters** (founder extension 2026-07-05): num_ctx (4k/8k/…), thinking on/off/budget, sampling (temp/top_p — temp≠0 discouraged: it inflates repeat variance), turn cap | Model IDENTITY stays pinned per arm (qwen3.5:9b @ digest) — a different model/quant is a NEW ARM, never a mutation |
| **Stage E only:** entrance-LLM refinement (tag classification, labeled summaries, fact extraction) as ingest-side mutations — see §4.7 Stage E | LLM-generated content is FROZEN OUT of Stages 0–C (deterministic first — attribution stays clean) |

### 4.4a Reader shape: a BOUNDED agentic loop, not single-shot

The reader answers each question as an **agent**: multiple turns against the DB (tool calls
and/or the guarded SQL hatch), observing results between turns, until it answers or hits the
bounds — because that is exactly how the production reader will run, and v2's benchmark was
agentic too. Bounds (mutable via §4.3): **max 8 tool turns**, per-question token budget,
wall-clock timeout; hitting a bound forces a final answer (graded as-is, never excused).
**`turns_to_answer` is recorded per question** — it is a schema-quality diagnostic, not just a
cost metric: the working hypothesis is that good derived-layer mutations REDUCE the turns a weak
reader needs (a one-hop projection turns a 5-turn fumble into a 1-turn lookup). A mutation that
raises accuracy but doubles median turns is a smell the ledger must show.

### 4.4 Scoring (the metric vector + gates)

Per question, graded **deterministically** (numbers/dates/IDs/enums/entity-ids exact; citation =
returned evidence id actually supports the answer; NO substring grading — v2's inflation lesson):

- `answer_accuracy` (strict) — primary
- `evidence_fidelity` — answer cites correct supporting rows
- `unsupported_claim_rate` — **hard gate ≤2%** (fabrication is worse than silence)
- `empty_behavior` — says "no data" when the gold answer is "no data" (v2: every model fabricated)
- `ambiguity_behavior` — returns candidates, not a silent wrong top-1
- `latency_p50/p95`, `tokens_per_question` — cost lens, tie-breaker
- **hard gate: permission/tenancy safety = 100%** (probe questions that try to read outside
  scope must all refuse; any leak auto-fails the iteration) — trivial pre-PF-01 (one org, one
  mailbox) but the probes are in the set from day one so the gate is inherited by later phases

Composite: lexicographic — gates first, then `answer_accuracy`, ties broken by
`unsupported_claim_rate` ↓, then tokens ↓. (Weighted scores hide gate regressions; lexicographic
can't.)

### 4.5 Question set + stopping conditions

- **150–300 gold Q&A** on the real corpus (study §6(k) params), authored once, frozen, versioned.
  Mix per the study's workload rubric: temporal / point-lookup / multi-hop / aggregation /
  fuzzy-semantic / no-data traps / ambiguity traps / permission probes. BG + EN (+ some DE).
  Split **60% dev / 40% holdout** (dev drives the loop; holdout only at checkpoint — schema
  overfitting to specific questions is THE validity threat to this design).
- **Stop when:** target reached (**≥85% strict accuracy on holdout** for the primary reader — the
  provisional usefulness bar, revisit at sign-off) · OR **8 consecutive rejected mutations**
  (plateau) · OR budget cap hit (**provisional: €50 API spend / ~30 loop-hours per stage** —
  Yani to confirm) · OR holdout diverges >5pp below dev twice (overfitting — stop and prune).

### 4.6 Reader models (the "which LLM" decision — proposal)

| Arm | Model | Role | Note |
|---|---|---|---|
| **PRIMARY** | Local 7–8B via Ollama: **Gemma 4** (Apache-2.0; repo already has `LLM-04_gemma_4_reference.md`) or **Qwen3 8B** — pick ONE at kickoff, pin the exact tag | The loop's optimization target | This is the SOVEREIGN reader the whole strategy bets on; zero egress, zero per-call cost, unlimited iterations |
| SECONDARY (reference) | **Gemini 3.1 Flash-Lite** (`gemini-3.1-flash-lite`, $0.25/M in · $1.50/M out, 1M ctx) | Scored at baseline + each checkpoint only — a cheap cloud calibration point ("is the schema or the model the ceiling?") | **EGRESS DECISION REQUIRED**: the corpus is a real mailbox incl. third-party PII. Options: (a) skip the cloud arm (fully sovereign experiment), (b) accept dev-egress on the founder's own corpus under Google's paid-tier no-training terms, (c) build an anonymized question subset for the cloud arm. Recommend (a) or (b) — (c) is real work for a reference number. Yani decides. |

Temperature 0, fixed seeds where supported, N=3 repeats (report mean + worst); model + quant +
runtime versions pinned in the ledger header. Optimizing for Flash-Lite instead of the local
model would optimize for the wrong target — the cloud arm never drives accept/reject.

**Hardware reality (measured on this machine 2026-07-04):** RTX 4060 Laptop **8 GB VRAM** ·
Core Ultra 9 185H (16C/22T) · 31.4 GB RAM · Ollama **not yet installed** (kickoff setup item).
Consequence: a **7–8B model at Q4_K_M (~5 GB) runs fully on-GPU** with room for context —
comfortably 30–60 tok/s, which is what the loop needs (hundreds of questions × 3 repeats ×
dozens of iterations at zero marginal cost). A 12–14B Q4 is possible with partial CPU offload
(slower — acceptable for checkpoint arms, not the inner loop). The §8 fallback arm
(Qwen3.6-35B-A3B MoE, ~3B active) would run mostly from RAM — usable for a one-off verdict,
not for looping. The machine therefore enforces the honest experiment: the primary reader IS
the 7–8B sovereign class, not a comfortable bigger stand-in.

### 4.7 Pre-registered mutation backlog (Stage order = plan §10 phasing)

- **Stage 0 — baselines (no mutations):** B0 raw schema + free SQL agent (the v2 reproduction);
  B1 current schema + one naive `search` tool. These anchor the whole curve.
- **Stage B — deterministic spine (expected big jumps):** identity merge (study §8.1) → thread
  projection + reply-obligation → language population → content-dedup pointers → first-degree
  edges → scalar rollup columns for the temporal/point-lookup mass.
- **Stage C — tool/interface ergonomics (the loop's home turf):** typed tool set (~6, study
  §6(g)) vs the hero-tool collapse; answer-packet shapes; candidates-vs-top-1; payload token
  budget; tool descriptions wording; schema-serialization variants (M-Schema-style vs terse);
  SQL hatch on/off + repair pass; trgm/unaccent alias resolution; index sweeps.
- **Stage D (only after Ask exists — separate run):** chunking params, prefix A/B, fusion
  weights, exact-vs-HNSW — the study's §10 open tensions.
- **Stage E — entrance-LLM refinement mutations (the raw+refined bet, measured):** ingest-side
  enrichment runs over the lab corpus as mutations — tag classification into the controlled
  taxonomy, labeled thread summaries (NEVER embedded, never blended as evidence — plan §9
  resolution 2), deterministic-then-LLM fact extraction. Rules: (a) runs AFTER the deterministic
  spine is optimized, so its lift is measured ON TOP of the best non-LLM baseline — this is the
  direct test of the "ingest-time bounded refinement" reframe from v2; (b) **subsample-first
  economics** — a full-corpus enrichment pass costs hours even locally, so mutations trial on a
  ~500-email stratified subsample and promote to full corpus only on measured lift; (c) outputs
  live under the study's trust-zone contract (provenance, labeled, untrusted-claims zone);
  (d) the enrichment model runs SANDBOXED (tool-less, schema-constrained — email is adversarial
  input, study §5); (e) **the verifier's gaming audit extends to enrichment prompts** — an
  enrichment prompt is itself a channel for question-encoding ("extract whether the deadline was
  confirmed" is memorizing question #12 by other means) and gets the same CLEAN/SUSPECT/GAMED
  verdict as a schema diff.

### 4.8 The independent verifier (anti-Goodhart / anti-reward-hacking)

The loop operator both PROPOSES mutations and MEASURES them — a built-in conflict of interest.
A **separate Claude subagent with an isolated context** (it never sees the operator's reasoning
or ledger notes — only the artifacts: mutation diff, frozen question file, scores JSONL, lab DB)
verifies independently:

1. **Score verification** — re-runs the eval harness itself on every ACCEPTED iteration (sampled
   dev subset at accept-time; the FULL dev + holdout at each checkpoint) and confirms the
   operator's reported scores reproduce within the N=3 variance band. Catches harness bugs,
   grading drift, and "creative" score reporting alike.
2. **Gaming audit** — adversarially inspects each accepted mutation's diff with one question:
   *does this structure encode knowledge of specific eval questions rather than general corpus
   structure?* Red flags: literals/entities that appear in the gold set, projections whose shape
   mirrors one question, tool descriptions paraphrasing questions, filters matching question
   wording. Verdict **CLEAN / SUSPECT / GAMED** with evidence; GAMED reverts the mutation even
   if scores improved — and SUSPECT freezes it until a generalization probe passes.
3. **Generalization probes** — periodically authors NOVEL paraphrase/perturbation questions
   (same intents, different wording, entities outside the gold set) and runs them as a
   spot-check reported separately (never added to the frozen scored set). This is the
   entity-level-overfitting detector the same-corpus holdout structurally cannot be.
4. **Invariant checks (mechanical)** — question-file hash unchanged; mutation diff touches only
   the MUTABLE plane (§4.3); raw-layer/RLS/erasure untouched; ledger row complete.

The ledger gains a `verifier` column (verdict + re-run delta). **No stage is declared done
without a checkpoint-level verifier pass.** This mirrors the repo's standing workflow rule —
every agent finding is an unproven claim until independently verified — applied to the loop
itself.

**Materialized (2026-07-05):** the verifier exists as the custom subagent
`.claude/agents/exp001-verifier.md` — **model: Opus**, tools Read/Grep/Glob/Bash, read-only +
sealed-set-forbidden + hostile-artifact rules baked into its system prompt. Its duty list adds
the founder's explicit mandate as Duty 3: a standing **bias-of-code-toward-data audit** (harness
prompts/tool descriptions/SQL magic numbers/grader tolerances quietly specialized to this corpus
or question set — the accumulated bias no single diff shows), alongside score re-verification,
per-mutation gaming audits, generalization probes, and mechanical invariants. This also settles
the §4.15 critic-model decision as **option A (Claude-family judge)**: the founder picked Opus —
a strong pinned judge from a different family than the reader, per the no-self-preference rule.

### 4.9 Logging & telemetry — the loop's memory (what makes iteration N+1 smarter than N)

Three levels, all append-only, all committed with the iteration, all machine-readable (JSONL) so
scores and analyses are re-computable offline and the verifier works from artifacts alone:

1. **Per-question trace (the atom):** question id + category · the FULL agent transcript —
   every turn: tool called, arguments, returned payload (truncated + hashed), tokens, latency ·
   final answer · grade + **failure tag** · `turns_to_answer` · tables/tools touched. The
   failure tag is a closed taxonomy: `wrong_tool` / `wrong_entity_resolved` / `synthesis_error`
   / `missing_structure` (the data to answer isn't reachable in one hop) / `hallucination` /
   `turn_cap_hit` / `empty_mishandled` / `ambiguity_collapsed` / `grader_dispute`.
2. **Per-iteration record (the ledger row, enriched):** mutation + its one-line hypothesis +
   diff ref · score vector + delta vs parent · **per-category breakdown** (temporal /
   point-lookup / multi-hop / aggregation / fuzzy / traps) · **the question-flip lists** —
   which questions went pass→fail (regressions hidden inside an aggregate gain!) and fail→pass ·
   failure-tag histogram delta · verifier verdict + re-run delta · cost + wall time ·
   reproducibility pins (model tag + quant, seeds, harness hash, question-set hash, DB snapshot
   ref).
3. **Run-level curves (regenerated from 1+2, never hand-kept):** accuracy-over-iterations (dev
   vs holdout), median-turns evolution, per-category heatmap, cumulative cost.

**The operating rule that makes this pay:** before proposing mutation N+1, the operator MUST
read the current failure-tag histogram and flip lists and name the cluster it is attacking
("11 of 14 multi-hop failures are `missing_structure` on reply-chains → propose the thread
projection"). A mutation not traceable to observed failures is a guess — allowed, but flagged
`speculative` in the ledger so the accept-rate of evidence-driven vs speculative mutations is
itself measurable. Failure clusters ARE the prioritized backlog; the taxonomy is the loop's
steering wheel.

### 4.10 The experiment archive — one dossier per attempted architecture

Separate from the runtime telemetry (§4.9): a durable, browsable knowledge base of everything
ever tried. Structure:

```
docs/experiments/EXP-001/
  INDEX.md                          ← master table across ALL runs: every mutation ever tried —
                                      name · category · stage · score delta · turns delta ·
                                      verdict (accepted/reverted/GAMED) · run/iteration link
  runs/<run-id>/
    RUN_SUMMARY.md                  ← the run's curve, final scores, stage conclusions
    iterations/NNNN_<slug>/
      README.md                     ← THE DOSSIER (one per mutation, auto-generated skeleton):
                                      1. the architectural idea, in one paragraph
                                      2. WHY tried — hypothesis + the failure cluster attacked
                                      3. WHAT was made — DDL/diff summary (full diff linked)
                                      4. EXACT result — score vector before→after, per-category
                                         deltas, question flips, turns delta
                                      5. verdict + WHY it worked/failed (operator's analysis —
                                         the one human-written paragraph that matters most)
      mutation.sql / mutation.diff  ← the exact change, replayable
      scores.json                   ← machine-readable before/after
      verifier.md                   ← independent verdict + evidence
```

Rules: **nothing is ever deleted** — a reverted or GAMED mutation keeps its dossier (dead ends
prevent re-dying); the INDEX row is written at iteration close, win or lose; **the operator must
check INDEX before proposing** (a previously-failed mutation may only be retried with a stated
reason why conditions changed — e.g. "failed pre-identity-merge, retry on top of it"); a
mutation promoted into the production build cites its dossier in the PR (the measured-lift
evidence trail). The archive is the answer to "what was tried, what exactly happened, and why"
— without re-running anything.

### 4.11 Runner mechanics

The operator is a Claude Code session driving the loop (the `/loop` skill or a plain long
session — operator choice at kickoff); the eval harness is a plain pytest-style runner emitting
JSONL per question (model answer, grade, tokens, latency) so scores are re-computable offline.
Working artifacts (dump, raw JSONL, scratch) live under `experiments/lab/` in the worktree;
the durable dossiers + INDEX (§4.10) sync into `docs/experiments/EXP-001/` at every checkpoint —
so the knowledge base survives even if the lab worktree is discarded. Nothing else enters the
production tree until a mutation is explicitly promoted.

### 4.12 Codex cross-vendor gap review (2026-07-04) — verdicts and adopted revisions

An independent GPT/Codex review of this design returned 6 BLOCKERs + 9 IMPORTANT + 1 NICE.
Load-bearing factual claims were verified before adoption (the `.gitignore` no-ingested-data
rule is real; the Gemma 4 reference doc exists in the EXTERNAL tech-modules folder, not this
repo — both this doc's and Codex's wording corrected). Verdicts:

**Blockers — all ACCEPTED:**
1. **Holdout was adaptive, not held out** (checked every 5 accepts + drives stopping = leakage).
   → THREE partitions: visible **dev** (steers mutations) · **validation** (aggregate-only
   feedback, drives stopping) · **SEALED TEST** (grouped by thread/entity/time so shared
   entities don't leak across splits; run ONCE at stage end, twice absolute max; question text
   never exposed to operator or verifier during the loop).
2. **The archive would have committed real-mailbox PII to git** (transcripts, payloads,
   questions — violating the repo's own `.gitignore` rule). → ALL data-bearing artifacts (dump,
   questions, transcripts, payload fragments, evidence) live in an encrypted local artifact dir
   OUTSIDE git with defensive ignore rules; git receives only aggregates, opaque question ids,
   hashes, sanitized diffs, conclusions. Cloud reference arm cut from the pilot.
3. **"Deterministic grading" was under-specified for open-ended answers.** → Every question is
   authored as a TYPED ANSWER SCHEMA (canonical fields + acceptable alternatives, evidence-id
   sets + required coverage, numeric/date tolerances, answerable/ambiguous/no_data states,
   atomic claims each linked to evidence), and a **grader conformance suite** (known correct /
   partial / unsupported / ambiguous / adversarial answers) must pass BEFORE any model runs.
4. **Attribution confound** (scaffold + schema + enrichment mutating in one plane). → The reader
   scaffold (model, system prompt, turn policy, repair, answer format) is FROZEN through Stages
   0–C; scaffold tuning becomes its own later stage; a mutation = one deployable vertical
   capability (projection + the minimal tool exposure to use it); **identity merge is
   implemented as a derived reversible mapping table — source rows untouched** (which is what
   the study's assertion-ledger design wanted anyway). **Founder amendment (2026-07-05):
   model PARAMETERS are also mutable** (num_ctx, thinking mode/budget, turn cap, sampling) —
   attribution is preserved by CATEGORY-TAGGING every mutation (`schema` / `tool-interface` /
   `model-params` / `enrichment`), keeping the one-change-per-iteration rule, and reporting
   ablations per category; the experiment's headline claim becomes whole-system optimization
   for the pinned reader, with per-category lift attribution. Model identity/quant stays pinned
   per arm. Practical note: thinking on/off is expected to be the highest-leverage single
   param (measured 3–5× latency; accuracy effect unknown → an early Stage-C mutation pair).
5. **Forward selection can't rank mutations** (lift is order-dependent). → Pre-registered
   dependency DAG; prerequisite BUNDLES allowed when components are useless alone;
   **leave-one-out ablations on the final accepted stack** + 2×2 interaction tests for the
   strongest pairs; lift reported as path-conditioned unless ablation supports causality.
   Plateau rule SOFTENED, not removed: 8 consecutive rejections triggers founder review, never
   auto-stop (some stopping heuristic must survive for budget sanity).
6. **Question authorship had no bias control.** → Corpus-first protocol: freeze category quotas
   from intended user workflows → randomly sample threads/episodes from the immutable corpus →
   author natural questions WITHOUT seeing schema/tool representations → separate gold-answer
   pass from source evidence → seal the test partition before the operator ever sees it;
   time-separate the two passes when one person does both.

**Important — all ACCEPTED:**
7. Small-sample statistics: paired per-question comparison vs parent (same seeds), bootstrap CI
   + predeclared minimum effect to accept; ≥85% is an exploratory point target with CI, not a
   proven threshold, while the set is ≤300 questions.
8. **Per-category floors** (multi-hop / no-data / ambiguity can't hide under easy lookups) + a
   deterministic **oracle-tool-consumer baseline** separating "data unreachable" from "reader
   weak".
9. **Environment qualification run before Stage 0**: exact model digest + quant + template +
   context/KV-fit verified via `ollama ps` residency, tok/s, p95, and a 2–4h thermal soak on
   this laptop; Gemma-4-class and Qwen3-8B are NOT equivalent capacities — pin one digest.
10. Runtime honesty: a 30–50-question harness pilot MEASURES per-episode time first; the stage
    budget is derived from measurement (270–540 episodes per mutation is 1–9h at plausible
    speeds — the provisional 30h/stage guess is void); GPU-hours + operator time tracked.
11. **Revert hygiene**: every candidate runs on a disposable DB cloned from an immutable
    accepted-state snapshot (no down-migrations trusted); accepted state rebuilt by replaying
    migrations from baseline; dump hash, image digest, migration head, schema hash + row
    checksums recorded; background workers disabled.
12. **Verifier limits owned**: its verdict is ADVISORY — the sealed test is the real anti-gaming
    control; the scorer it uses is separately implemented + validated against the grader
    conformance suite; all artifacts treated as hostile quoted data (prompt injection); verifier
    is read-only + no-network + never sees the sealed set; founder reviews SUSPECT/GAMED only.
13. **Permission gate relabeled**: single-org/single-mailbox probes prove NOTHING about PF-01 —
    permission fidelity is explicitly OUT OF SCOPE for EXP-001 (production promotion remains
    PF-01-gated); the 100% gate stays only as a mechanical tenancy-invariant check.
14. **Stage E split out** into its own experiment (EXP-002 when designed): extraction
    precision/recall on a dedicated labeled set FIRST, then one full-corpus enrichment pass
    compared against the frozen benchmark — a 500-email subsample is not comparable to a
    full-corpus gold set. **Founder rules (2026-07-05, binding):** (a) **DB-first hard gate** —
    enrichment may not start until the deterministic-only optimization is EXHAUSTED and its
    plateau measured/accepted as the frozen baseline; every enrichment lift is reported relative
    to that DB-only ceiling. (b) **Granularity ladder, coarse-first** — refinement starts at the
    WHOLE-THREAD level (~1.5–2k threads: the natural unit of meaning for the questions that need
    enrichment, 3–4× fewer calls, no re-processing of quoted text); descend to PER-SINGLE-EMAIL
    refinement only as the END-OF-LADDER fallback if thread-level measurably fails to lift
    scores. Compute split: reader stays LOCAL (production fidelity); enrichment calls may run on
    the API (Together, retention off, founder-corpus egress accepted 2026-07-05) with a local
    re-enrichment parity check on a few hundred emails before any Stage-E conclusion is trusted.
15. **Durability is a program, not a session**: a deterministic state-machine CLI owns the loop
    (atomic per-question checkpoints, heartbeat, bounded retries, disk/thermal probes, resumable
    manifests, explicit failure states); Claude proposes mutations and analyzes — it does not
    own process survival at 3am. Windows note: native Ollama serves on localhost:11434; a
    dockerized harness reaches it via `host.docker.internal`.

**Nice — ACCEPTED with one modification:** one machine-readable event log is the single source
of truth; the ledger, dossiers, and INDEX are GENERATED from it at checkpoints (the §4.10
archive requirement survives — as generated views, never hand-kept duplicates); full dossiers
only for accepted mutations + genuinely informative failures; per-accept FULL verifier re-runs
cut from the pilot (sampled re-scores stay).

**Codex confirmed as sound** (kept unchanged): raw/derived separation, deterministic-before-LLM
ordering, local-first PII posture, bounded agent turns, candidates-not-top-1, explicit
empty/ambiguity behavior, flip lists, failure taxonomy, append-only metrics, second-corpus
requirement before generalization claims.

**Kickoff order (the review's "first three changes", adopted):**
1. Rewrite the evaluation protocol (authorship controls, typed graders + conformance suite,
   grouped three-way split, sealed test).
2. Secure-artifacts + disposable-DB execution BEFORE any mailbox data is copied.
3. The 30–50-question environment/harness pilot on ONE pinned local model (residency, speed,
   thermals, variance, resume) — budgets and targets set from its measurements.

### 4.13 Workspace layout (2026-07-04 addendum — supersedes §4.1's worktree and §4.11's paths)

The experiment is a **fully self-contained world OUTSIDE the MVP repo** — the real codebase is
physically untouchable from inside the lab:

```
C:\Users\Yani_\Desktop\In-Progress\One AI\Experiments\
  Loops\
    EXP-001-schema-loop\              ← one subfolder per experiment
      code\                           ← CLONE of the MVP repo PINNED to a named commit,
                                        git remote REMOVED (cannot push, by construction);
                                        the pin is recorded in run metadata — "lab built
                                        from <commit>" is always answerable
      db\                             ← lab docker-compose (its own project name + volume,
                                        Postgres on 55433), dump + restore/reset scripts,
                                        per-candidate disposable clones (§4.12.11)
      harness\                        ← runner CLI (the §4.12.15 state machine), graders +
                                        conformance suite, agent scaffold config
      questions\                      ← gold sets + typed answer schemas (dev/validation/
                                        SEALED — sealed kept separately, operator-inaccessible)
      artifacts\                      ← event log (source of truth), transcripts, scores —
                                        the PII-bearing zone; encrypted at rest if feasible
      archive\                        ← generated dossiers + INDEX (§4.10) — lives HERE,
                                        never in the MVP repo (Codex B2)
```

What flows BACK to the MVP repo (`docs/experiments/`): this design doc, checkpoint conclusions
written into it (§5–§7), and a SANITIZED index snapshot (aggregates, opaque ids, no content).
What flows back to production CODE: only explicitly promoted mutations, re-applied as normal
PRs against the real repo, each citing its dossier. Nothing else crosses the boundary in either
direction — the lab reads a frozen copy, the repo receives conclusions and wins.

### 4.14 Sequential screening ladder (2026-07-04 addendum — the cheap-first eval)

Running the full dev set per mutation is the loop's dominant cost (§4.12.10). Adopted: a
**racing-style screen** — evaluate in stages, spending full runs only on survivors:

```
GEOMETRIC CUMULATIVE LADDER (successive-halving shape; founder revision 2026-07-05):
Rung 1:  12 stratified questions, PAIRED parent-vs-candidate, same seeds
Rung 2:  +12 fresh   (24 cumulative)   ← previous rungs' answers are REUSED, only the
Rung 3:  +24 fresh   (48 cumulative)      new questions cost episodes — each rung roughly
Rung 4:  +48 fresh   (96 cumulative)      doubles evidence for the cost of one prior rung
Full:    the ENTIRE dev set, paired, bootstrap CI — the ONLY place a mutation is ACCEPTED

At every rung: pooled paired delta vs pre-declared thresholds → REJECT (clear loser) or
ESCALATE. Rejection thresholds are GENEROUS at low rungs (kill only obvious losers on thin
evidence — a false kill of a good mutation is the compounding risk of a many-rung ladder)
and tighten as the cumulative sample grows.
```

**Founder correction (2026-07-05, Campaign 1): the ladder governs the PARENT/BASELINE too.**
No upfront full-set baseline: the parent is evaluated LAZILY — for each rung, parent and
candidate run on the SAME sampled questions (both cached forever by (config, qid)), so parent
coverage accumulates rung by rung and a full-96 parent run only ever exists where an accept
gate or checkpoint forced it. First mutation lands within the hour instead of after a 2-hour
baseline toll; the failure histogram steers from partial (rung-sized) evidence and sharpens as
coverage grows.

Four rules make this statistically honest (a raw random-10 verdict would be a coin flip —
SD ≈ 14pp at 10 questions): (1) **paired on identical questions** — removes question-difficulty
variance, the dominant noise source; (2) **stratified sampling at every rung** — no category can
be invisible; (3) **rungs only reject or escalate, NEVER accept** — sequential looks inflate
false positives, so every acceptance still flows through the single full-dev paired run
(§4.12.7); (4) **cumulative evidence** — later rungs pool ALL answered questions so far (prior
rungs' episodes are cached and reused), so each escalation buys double the evidence for the
marginal cost of the new questions only. Thresholds are set generously toward escalation at low
rungs (a rung's job is to kill obvious losers cheaply, not to find winners — with many rungs the
compounding risk is falsely killing a good mutation on thin evidence). Expected saving: 60–80%
of loop episodes; exact per-rung thresholds calibrated from the §4.12 pilot's measured
per-question variance.

### 4.15 Hybrid grading: deterministic fields + a narrowly-scoped LLM critic (2026-07-04 addendum)

String comparison is refuted (v2's substring grading is why small models scored 0–7% while fair
grading showed ~73%). But LLM-judges-everything has the inverse failure INSIDE an optimization
loop: the fitness signal becomes the judge's opinion, and mutations evolve to PLEASE THE JUDGE
(Goodhart, one level up). Adopted: a two-tier grader —

1. **Deterministic tier (first, and the majority):** the typed answer schema (§4.12.3) grades
   everything mechanically gradable — numbers/dates with tolerances, IDs, enums,
   answerable/ambiguous/no_data states, evidence-id coverage. Questions are AUTHORED to maximize
   this tier.
2. **LLM-critic tier (only the residue):** free-text claims are graded as **claim-by-claim
   binary entailment against the gold atomic claims** ("does the answer assert this claim —
   yes/no", with the gold in hand) — never open-ended 1–10 quality scoring, never without the
   gold. Guardrails: the critic is a STRONG pinned model from a DIFFERENT family than the reader
   (Claude-class judging a Gemma/Qwen reader — no self-preference), temperature 0; it must pass
   the grader conformance suite (known correct/partial/unsupported/adversarial answers) before
   any run and is re-checked at every checkpoint; verdicts are CACHED by (question,
   answer-hash) — identical answers across iterations reuse the verdict, which both saves cost
   and makes grading stable; critic-vs-deterministic disagreements and low-confidence verdicts
   get the `grader_dispute` tag → human adjudication, and the dispute RATE is itself tracked
   (a rising rate = grader drift alarm). Anti-Goodhart backstops: the critic never sees which
   mutation produced an answer; the verifier sample-audits critic verdicts; and the sealed test
   plus human spot-checks remain the final arbiter of whether judge-pleasing drift crept in.

## 5. Results

*(not run — design only)*

## 6. Analysis

*(not run)*

## 7. Decision

*(pending — the design itself needs Yani's sign-off on: the ≥85% target, the budget cap, the
primary local model, and the Flash-Lite egress option a/b/c)*

## 8. Follow-ups (pre-registered)

- If the plateau lands BELOW the usefulness bar on the 7–8B: re-run Stage C with a 14–30B local
  model (Qwen3.6-35B-A3B class) before conceding the sovereign bet.
- Promote each winning mutation into the real build as its own PR with the measured lift in the
  description (the loop ledger is the evidence trail).
- The harness + question set graduate into CI (regression gate for every future schema change —
  the plan's "benchmark is the governing artifact").
- A second corpus (different tenant shape: newsletter-heavy, DE-dominant) to test that accepted
  mutations generalize — guards against single-corpus overfitting the holdout can't catch.
