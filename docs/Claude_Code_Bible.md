# The Claude Code Bible — How We Ship With Agents

Distilled from a real production engagement (a multi-tenant veterinary clinic SaaS, ~2,500
backend tests, GDPR-bound, contract-bound), August 2026. Everything here was paid for on live
code — every rule exists because its absence bit us. This document is project-agnostic: it
names the method, the standards, and the exact prompts/roles, so any new project can adopt it
on day one.

**The one-sentence version:** move the quality gate as early as possible (design → contract →
sealed tests), separate the builder from the judge at every step, trust artifacts never
self-reports, and bind every rule to a mechanical check.

---

## Part I — Core principles

These are load-bearing. Every phase in Part II is an application of one of them.

1. **The builder is never its own judge.** Whoever writes code does not decide whether it is
   correct: acceptance tests are written by a different (stronger) model before the code
   exists; reviews are adversarial and adjudicated by a separate actor; the orchestrator
   re-runs the decisive checks itself. Research background: agents given control of both the
   implementation and the judging surface demonstrably reward-hack (test-file edits,
   hardcoding, visible-vs-held-out gaps of 40+ points). Never let "the agent says its tests
   pass" be the release artifact.

2. **Findings are CLAIMS, never ground truth.** This applies to review findings, to external
   tools (Codex/GPT), **and to human bug reports**. Every claim is verified against the
   source before a fix is written. Refuting a claim with evidence is as valuable as fixing
   one. (Live example: a reported "the AI draft disappears" bug was proven *impossible* by
   code-reading — the real defect was a lying list badge plus a frontend/backend disagreement
   about what "empty note" means. Fixing the reported symptom would have fixed nothing.)

3. **Trust artifacts, not self-reports.** Exit codes, test logs, SHA-256 hashes, diffs
   against a sealed baseline, coverage percentages, DB rows. When an agent reports "all
   green", the orchestrator re-runs the suite itself before telling the human. When an agent
   inherits partial work from a dead predecessor, it says so explicitly ("I did not author
   this; I audited it") so downstream review can calibrate.

4. **Every rule gets a mechanical guard.** A rule that lives only in prose decays. House
   rules live in versioned files (`.claude/rules/*.md`) that every agent prompt points at,
   and wherever possible each rule is backed by a test that fails when the rule is broken:
   coverage gates in CI, file-length ratchets, "no second normalizer" source scans,
   ignore-list parity tests, PII log guards driving real flows, boot-without-the-domain-module
   CI tests. The rules the previous production system only *declared*, the new one *enforces*.

5. **Owner decisions are recorded the moment they are made.** The project's `CLAUDE.md` is a
   decision ledger: every approved scope, every deliberate deviation, every "we discussed X
   and chose Y because Z" goes in immediately, with the date. Documented deliberate behavior
   protects against future agents "fixing" intentional design. Genuine forks go to the owner
   as concrete options (with ASCII previews where visual); everything else is decided,
   stated, and proceeds — asking permission for reversible work is a failure mode.

6. **Modular architecture is an AGENT-ERGONOMICS decision, not just software engineering.**
   The application decomposes into modules for two reasons that compound: (a) **blast
   radius** — a mistake born inside one module stays inside it; and (b) **working-set
   economy** — when the work lives in one module, the agent loads few dependencies, reads
   few files, and holds few invariants in its head, so it reasons better, errs less and
   costs less. The quality of agent output scales inversely with the size of the working set
   it must understand. Every technique in this document (recon-by-template, implementation
   waves, sealed module-shaped contracts) works BECAUSE the modules are small and their
   boundaries are real. The full strategy — the grain, the boundaries, the cross-cutting
   rules — is the first chapter of Part III.

7. **No dead seams.** No event without a consumer, no table without a writer, no endpoint
   without a caller, no "deferred" pipeline step — all wired in the same change set or not
   built at all. (Production lesson: "1 publish, 0 subscribers" and "embedding deferred"
   meant *never*.)

8. **Move quality left.** The cheapest defect is the one the contract refused to admit. The
   sealed-oracle experiment measured it directly: a normal build round produced 4–6 confirmed
   review findings needing a fix stage; the sealed round produced ZERO — green on the first
   oracle run — at roughly a quarter of the token cost, because the find-fix loop after the
   build simply never happened.

---

## Part II — The delivery lifecycle

The phases in order. Small fixes may skip to Phase 6–8; anything substantive walks the whole
ladder. Phases 2–5 (the sealed-oracle path) fit **contract-shaped work** — API surfaces,
numbering/counters, renderers, parsers, migrations with crisp semantics. UI work and fuzzy
exploratory features use the classic path: build → lenses → adjudicate → fix → gate (Phase 6
onward), because UI tests written before the components exist inevitably pin implementation
internals and the "unbiased" property is lost.

### Phase 0 — Design with the owner

- Discuss the feature in product terms. Produce a **visual mockup** where the deliverable is
  visual (an HTML artifact of a document/screen beats prose every time).
- Surface real forks as explicit options with a recommendation; record the chosen answers in
  `CLAUDE.md` with the date, including deliberate deviations from prior plans ("built on its
  own simple mechanic NOW, absorbed by the memory layer LATER — reason: …").
- Check the fix registry and the decision ledger before treating anything as a defect or
  re-opening a settled question ("do not re-open without new facts").

### Phase 1 — Recon (read-only scouts)

Before any build, parallel **read-only** agents map the terrain. Deliverables:

- **Seams with `file:line` anchors**: where the new work attaches, with short verbatim quotes
  of the signatures. Not "there is a service layer" but "copy `weight_service.py:127`'s
  create shape; the router pattern is `router.py:246-360`".
- **Templates-to-copy, named**: "the dashboard panel follows `draft-consultations-section`
  1:1, including its four render states and its 'own api module, own row type' invariants."
- **Bug diagnosis with evidence**: for every reported bug, the root cause proven by reading
  (repro conditions, the exact broken line, "is this documented deliberate behavior?"
  checked against the registry) — BEFORE any fix is written. Scouts report; they never edit.
- **Negative findings stated honestly**: "there is NO precedent for X anywhere in the
  codebase; building it is a new pattern and needs an owner call" is a first-class result.

### Phase 2 — The contract

For sealed-oracle work, the orchestrator writes a **contract document**: WHAT the system must
do, never HOW. Endpoints, verbs, exact response shapes, status codes, numbering semantics,
concurrency guarantees, privacy edges, audit expectations — plus an explicit instruction that
the test author must enumerate further edge cases the contract implies. No module names, no
library choices, no schema hints. The contract is the quality bottleneck of the whole method:
a vague contract yields either an impossible oracle or a hole-filled one. Write it after
Phase 0/1, when the semantics are genuinely settled.

### Phase 3 — The sealed oracle (test-first, strongest model)

A single **test-author agent** — on the strongest model available (we use the frontier-tier
model for this role even when implementation runs on a cheaper tier; judging quality is worth
more than building quality) — receives ONLY:

- the contract;
- the test environment (fixtures, seed factories, house test idioms, existing public
  surfaces used to arrange state);
- hard rules (below).

It must NOT receive implementation ideas, and its tests must not assume internals — only the
public surfaces named in the contract. Hard rules for the author:

1. Touch nothing outside the test directory (plus dependency manifest ONLY for a test-infra
   dependency, e.g. a PDF text extractor).
2. Every test must fail **for the expected reason** (404 for a missing route), never with a
   broken fixture / import / collection error. The author runs its files and delivers an
   **expected-failure map**: test → how it fails today, proven by a real run.
3. **Anti-vacuity**: no test may pass by accident against the missing feature. Sharp trick
   from practice: tests whose contracted answer IS 404 first prove a 200 on the caller's own
   resource, so they cannot pass against an absent route.
4. Cover the contract AND the edges it implies: concurrency (parallel first-issuances of a
   counter), boundary values (limits admitted at their own boundary — anti-off-by-one),
   privacy edges (an erased subject leaves only the tombstone), audit rows (present, clean of
   personal data, not duplicated), refusals that must not consume resources (a 409 never
   burns a sequence number), timezone edges (both DST seasons against a hardcoded-offset
   bug).
5. **List the underdetermined cases NOT written** ("the contract admits both 422 and no-op
   here; not sealed") — honesty about the oracle's edges is part of the oracle.
6. Lint/format clean. No commits.

### Phase 4 — The seal

The orchestrator (not the author) reviews the tests **for soundness only** (they fail for the
right reasons; they don't assume internals; zero `app.*` imports), then:

- commits them **locally** — red tests never get pushed (CI would burn); push happens after
  green, so CI only ever sees the green pair;
- records SHA-256 hashes of the sealed files separately;
- the commit message states: "sealed oracle, written BEFORE the implementation; the
  implementation may not modify these files."

The seal protects the **acceptance oracle**, not all tests: implementers may ADD their own
new test files (encouraged), but may not touch sealed ones by a single byte.

### Phase 5 — The implementation fleet

A workflow of implementer agents (we run Opus-tier here) builds against the sealed oracle:

- **Every prompt carries the prohibition verbatim**: "these two files do not change by one
  byte under any circumstances; if a test looks impossible or contradictory, do NOT touch it
  — report it verbatim and continue; a diff line in these files fails the whole experiment."
- **Waves respect file ownership and dependency order** ("core migration first, domain module
  second; do not touch files agent X owns"). Parallel agents get disjoint file sets;
  genuinely conflicting parallel edits get worktree isolation instead.
- House rules apply in full (Part III) — the oracle checks the contract, the rules check
  everything else.
- **Bounded green loop**: a separate oracle-runner agent executes ONLY the sealed files and
  returns the verbatim failure output; a fixer agent (production code only) iterates; hard
  cap on rounds (we use 4), then stop-and-report.
- **Mechanical verification by the orchestrator**: SHA-256 of sealed files vs the recorded
  hashes AND `git diff <seal-commit> -- <sealed files>` must be empty; then the orchestrator
  re-runs the oracle itself. Only then is "green" claimed to the human.

Measured result to expect when the contract and oracle are sharp: first-round green, zero
fixers, implementers voluntarily adding their own edge tests on top, and cross-surface
consistency (e.g. new audit actions propagated to the UI label map because a compile-time
invariant forces it).

### Phase 6 — Independent review (after green / after any build)

Two decorrelated channels, then adjudication:

**(a) Cross-vendor review (Codex/GPT).** Drive a GPT-family CLI as an independent reviewer —
different model family, different blind spots. Non-negotiables: it gets a context-rich brief
(project constraints, the change's INTENT, files that define the contract) and read-only
access; its findings are hypotheses. Every finding is reconciled against the source with
maximum rigor and classified **CONFIRMED / QUALIFIED / REFUTED** with evidence; severity is
re-decided by us; its proposed fixes are scrutinized (prefer canonicalizing over rejecting,
etc.). Egress is a real decision: sending source to another vendor requires explicit consent
and never includes tenant data or secrets.

**(b) Adversarial lens review (in-house).** Independent read-only reviewers in parallel,
each with ONE lens:

- **security / tenancy / privacy** — org-scope gaps (RLS + explicit filters + org in JOINs),
  personal data in logs/audit details, permission and consent gates, upload surfaces
  (decompression bombs — measured live: a 900 KB crafted .xlsx passing every size gate and
  ballooning in memory), HTML escaping, erasure interplay;
- **house rules** — layer violations, docstring lies, file-size caps, migration purity
  (core DDL never mixed with domain DDL), dead seams, barrel violations, literal colors in
  pages, N-transaction bulk operations;
- **functional-vs-spec** — every contract clause checked against the code, AND the tests
  checked for vacuity ("a test that proves the function but not the wiring is a finding").

Reviewers report ONLY defects with a concrete failure scenario (inputs/state → wrong
outcome). No style nits, no "consider". An empty list is a respectable result.

**(c) Adjudication.** A separate agent takes EVERY finding and adversarially tries to REFUTE
it against the actual source. Deduplicates. Discards refuted, speculative and by-design
findings (the spec, the rules and the registry document deliberate behavior). Re-judges
severity against the rubric (below). Marks each confirmed finding as living in committed vs
uncommitted code (this decides registry treatment). Only confirmed findings, WITH their
evidence, reach the fix stage.

**Severity rubric** (classification is never finally the finder's — deflation pressure is
real when an exit condition mentions "critical"):

- **critical** — the scenario crosses a hard boundary regardless of likelihood: cross-tenant
  read/write; personal data reaching logs, metrics, audit details or another tenant; an
  auth/permission/consent gate bypassed; loss or silent corruption of domain-critical data;
  unreconcilable billing.
- **major** — wrong behavior on a real path, no boundary crossed: a defect users hit in
  normal use, a spec-named invariant violated, a missing audit row, a resource-exhaustion
  vector, a vacuous seal.
- **minor** — real but contained: edge-path UX, dead seams, drift that cannot yet mislead.

When in doubt, take the HIGHER class and let adjudication argue it down with evidence.

### Phase 7 — Fixes and the registry

Every confirmed finding gets a fix **plus a sealing regression test**, named for the
INVARIANT (`test_logout_after_concurrent_rotation_leaves_zero_live_tokens`), never the
mechanism — and the seal is **mutation-checked**: revert the fix, watch the seal go red,
restore. A seal that stays green under mutation is vacuous and gets rewritten (watch for the
subtle vacuity: `startsWith(PREFIX)` passes even when the literal is re-hardcoded, because
the values are equal — a source-scan guard is the real seal there).

**The registry** (`docs/FIX-REGISTRY.md`) is the project's defect memory. Rules:

- A fix without BOTH artifacts (registry row + sealing test) is unfinished — not done, not
  committed.
- Before fixing anything, CHECK the registry — the "bug" may be documented deliberate
  behavior or a previously fixed area whose constraints explain the current shape.
- **Scope rule**: the registry records defects that SHIPPED (live in committed code). A
  defect caught in the run's own uncommitted code gets the sealing test but NO row. A
  committed twin discovered along the way DOES get a row.
- A fix that revises an earlier fix references the old ID; the old row is marked superseded.
  History stays visible. Sealing tests are never weakened to make later changes pass —
  changing a sealed invariant is an owner decision, recorded in the superseding row.
- Registry rows carry: scenario + root cause, the source that found it, the seal's name, the
  commit hash (filled by a follow-up commit once the fix ships; keep attribution honest —
  when we once stamped 82 old rows with the wrong hash, the correction was its own commit).

### Phase 8 — The full clean gate

A run is DONE only when the FULL suites pass on a CLEAN environment:

- Backend: the complete test suite against a **freshly created probe database** (migrated
  from zero), not the dev database with weeks of residue — statistics-dependent tests behave
  differently on lived-in data, and that difference once cost a night of phantom-regression
  hunting.
- Coverage gates as separate CI-equivalent passes: **100% on auth/authorization/tenancy/audit
  modules** (a hard floor — uncovered 403 branches are exactly what rots), a high floor on
  the domain (we use ≥90%), a base floor repo-wide.
- Frontend: full unit suite, typecheck, lint, format check, production build.
- Linters/formatters repo-wide (not just touched files — two of our registry rows exist
  because round gates ran targeted lint and HEAD went red for everyone).
- **Targeted green during iteration NEVER substitutes for the final full gate.**
- **Before diagnosing a red as a regression, check the environment first**: who else writes
  to the shared DB, what survived a kill, is the machine under load (a heavily loaded box
  produces `beforeAll`-timeout flakes that vanish on a calm run — prove flakiness by
  isolation + a calm rerun, then fix the ROOT if it recurs, e.g. pre-importing heavy chunks).
- Long gates run in the background with logs written to durable files (`tail > log; echo
  DONE`) so a dead agent cannot lose the result; a watchdog monitor tails for the DONE
  markers AND alarms on "no live test process for N minutes" — background agents DO stall
  between steps, and the cure is a nudge message, not waiting.

### Phase 9 — Commit discipline

- **NEVER commit or push without the owner's explicit instruction in the same turn.** Do the
  work, run the gates, leave the tree dirty, wait.
- Commit the exact gated state. If several rounds accumulated in one tree and later rounds
  edited earlier rounds' files, do NOT fabricate per-round commits — hunk surgery creates
  untested intermediate states and a broken bisect. One honest commit of the gated whole
  beats five plausible-looking lies. Say so to the owner when it changes a prior plan.
- Message: what shipped per round, the gate numbers, registry IDs. Trailer lines per the
  harness convention.
- Follow-up commit fills registry hashes. Push only green states.

### Phase 10 — The live-testing loop

Code green ≠ product right. The loop that catches the rest:

- **A gitignored scenario folder** in the repo: per-scenario documents (prep steps, a
  dialogue/script to perform, exact expectations, a checklist) + a FINDINGS journal. Testers
  walk scenarios; the journal accumulates evidence.
- **Analyze tester sessions from the TRACES, not the anecdotes**: read the actual DB rows
  (transcripts, drafts, versions, audit timestamps). Before declaring a miss, check timing —
  we once "lost" an interaction warning that turned out to be configured 15 minutes AFTER
  the AI ran; the correct verdict was "unevaluated, retest", not "missed".
- **Verification scenarios close findings**: after a prompt/instruction fix, a scenario
  exists whose explicit purpose is to re-test that finding; the journal records
  closed/reopened.
- **Record the EMERGENT WINS, not only the defects.** When analyzing AI behavior in the
  journal, unplanned successes are first-class entries beside the failures: the flag nobody
  designed that caught a real hazard, the honest "the record is empty" answer where a lesser
  system would have invented data. Two reasons this is discipline rather than
  self-congratulation: it CALIBRATES TRUST (you learn which behaviors the system earns
  autonomy on, with the same evidence standard as the failures), and it repeatedly surfaces
  PRODUCT VALUE nobody planned — several shipped features started life as an emergent-win
  journal line. A findings journal that records only failures teaches you only fear.

### The prompt-fix round (LLM behavior is versioned configuration)

Where the product itself contains LLM agents (generators, reviewers, extractors), their
instruction sets get the SAME discipline as code — because they are the code of the
behavior layer:

- **Instruction sets are versioned configuration files** (per-agent `.md` bodies living
  WITH the domain module that owns them), read from disk on every agent construction — so
  a prompt fix is live on the next run, no deploy, no restart. That immediacy is the
  superpower and the hazard: the gate below is what keeps it safe.
- **A live finding becomes a GENERAL calibration rule with the observed case as its worked
  example.** Never patch the instance ("Синулокс 250 mg is fine"); write the rule the
  instance instantiates ("when a dose is stated in MILLIGRAMS, convert to mg/kg through
  the context weight BEFORE judging it; in-range → silence — worked example: 250 mg ×2 at
  15 kg = 16.7 mg/kg, inside the label range, no flag"). The worked example pins the rule
  to reality; the general form makes it transfer.
- **Prompt changes are gated by their own suites** (golden-sample / print tests over the
  versioned prompt+schema+model pin) before being declared done — a prompt edit that
  breaks the extraction contract must fail a test, not a clinic.
- **Every prompt fix gets a VERIFICATION SCENARIO**: a scripted live case whose explicit
  purpose is to re-test that finding, recorded in the findings journal as closed or
  reopened. A calibration rule nobody re-tested is a hope, not a fix.
- The journal tracks finding IDs across rounds (Г-01, М-02…) exactly like the code
  registry tracks FIX-NNN — so a regression in behavior is recognized as a REOPENING, not
  discovered as a novel surprise.

### Audit mode — loop-until-dry, done honestly

For release sweeps and "find everything" audits (NOT for routine feature rounds):

- **Dry = K consecutive dry rounds through DIFFERENT lenses with fresh contexts** (we use
  2–3), never one round, never the same tired context whose round-1 conviction "I already
  checked that" leaks into round 5.
- **Canary calibration is mandatory wherever the exit is defined by NOT-finding.** "No
  findings" is a negative existential — indistinguishable from weak finders, and worse: when
  not-finding advances the goal, you have built a gradient toward shallow search. So plant a
  known, REPRESENTATIVE defect (a mutation of real code in a disposable worktree — an org
  filter dropped from a JOIN, an audit row moved out of its transaction, a consent gate
  skipped; never a trivial typo, never in the real tree). A round that catches the canary
  gives its dry verdict weight; a round that misses it is INVALID, not clean. Canaries never
  reach reports or the registry. Detection rate is the calibration metric; calibrate per
  audit, not per round (it doubles finder cost).
- **Ceilings, declared up front**: max rounds, token/time budget, scope. On exhaustion the
  loop STOPS AND REPORTS THE STATE — a human decides whether to continue. Exhaustion is a
  report, never a silent green.
- The finders never know the stopping rule — a separate orchestrator judges dryness.
- The symmetric upgrade for sealed oracles: mutation-test the ORACLE itself (plant defects in
  the implementation copy; the oracle's kill rate is the number that says how much its green
  is worth).

---

## Part III — The standards (house rules)

These live as separate files in the project (`.claude/rules/*.md`), every agent prompt points
at them, and each is CI-enforced where possible. Summarized here in full so a new project can
transplant them.

### Architecture — the modularity strategy (agent ergonomics first)

This is the load-bearing chapter: every phase in Part II works because of the decisions
here. Modularity is treated as a strategy for HOW AGENTS WORK, with classical software
benefits as the welcome side effect.

**Why modules, stated the agent's way.** Two benefits that compound:

1. **Contained blast radius.** A mistake born inside one module cannot reach the others: its
   tables are its own, its routers mount through its own barrel, its migrations are its own
   revisions. When an implementation agent goes wrong, the damage has a fence around it —
   and review/fix agents get a bounded search space instead of a whole repo.
2. **Working-set economy.** An agent working inside one module loads few dependencies, reads
   few files, and holds few invariants in its head. That is not a convenience — it is the
   main quality lever: agent reasoning degrades with the size of the working set it must
   understand, so the architecture's job is to keep that set small BY CONSTRUCTION. A
   focused agent takes fewer wrong assumptions into the code, needs cheaper recon, and fits
   more of the relevant contract into attention at once.

**The grain: modular MONOLITH — and the pendulum warning.** Modularity ≠ microservices.
The predecessor system ran a 6-service topology and an audit condemned it: services sharing
one database, exactly one real inter-service call, a stub gateway — all cost, no benefit.
Container boundaries add network seams, deployment surface and identity plumbing WITHOUT
shrinking the agent's working set (the coupling just hides in the shared DB). The right
grain: one app process, one worker on the same codebase, one public edge — and modularity
enforced at MODULE and TOOL boundaries inside the monolith. In-process function calls
between own modules (no HTTP to yourself, no internal API keys); a queue for heavy work;
WebSocket for streaming; a dead-letter table for bounded-retry failures.

**The boundaries, and their mechanical enforcement.** A boundary that is not enforced decays
into a suggestion:

- Layout: `core/` (the platform: auth, tenants, llm, observability, …) and domain modules
  (`<domain>/…`). **Domain imports core — NEVER the reverse.** Core must boot with any
  domain module absent, and CI PROVES it with a boot-without-the-domain test. Stripping a
  domain module leaves core fully functional — that same line is the IP/contract boundary
  when core and domain have different owners.
- Barrels are the doors: every module exports its public API through its barrel; external
  code imports from the barrel only. A reach into another module's internals is a boundary
  violation regardless of whether it "works".
- Tables are prefixed per module; a migration revision NEVER mixes core DDL and domain DDL
  (a mixed revision becomes inseparable forever); memory/audit-class tables are owned by
  exactly one module and accessed by others only through its service API.
- Declarative over code changes: a new agent, tool or nomenclature entry is configuration +
  registration through the module's seam, never an edit to core.

**Legibility: the docstring is the boundary's signage.** A module boundary the agent cannot
SEE forces whole-repo reads anyway. Every file opens with Role / Used by / Depends on / Key
invariants — so an agent reads one header and knows the neighborhood: what this file is for,
who calls it, what it leans on, and which behaviors are deliberate. Modularity and docstring
discipline are ONE decision: the fence and its sign. (A modular codebase with silent
boundaries is still illegible to an agent; a documented monolith is navigable. Do both.)

**Cross-cutting surfaces: the three-tier strategy.** Some files are shared by construction —
audit-action registries, router mount lists, barrels, label maps. They are where parallel
agents collide and where features leak into each other, unless:

1. **Design tier — make them APPEND-ONLY registries.** The best shared file is one where a
   new feature only ADDS a line: a new constant at the bottom, one `include` line, one
   export, one label entry. Append-only surfaces make parallel conflicts nearly impossible
   and merges trivial. The inversion is a diagnostic: if a new feature must EDIT existing
   content in a shared file, that is a smell — logic is living in the registry that belongs
   in a module. Registries declare; logic lives at home.
2. **Orchestration tier — one owner per wave.** In any parallel wave, a shared file is
   assigned to exactly one agent; every other prompt carries the explicit "do NOT touch X —
   another agent owns it". Two packages that must both touch it get SEQUENCED, not
   parallelized.
3. **Insurance tier — an exhaustiveness guard on every registry.** Type the label map as
   `Record<Action, Label>` (or seal it with a completeness test) so that adding a registry
   entry BREAKS the build until every consumer names it. Measured effect: implementation
   agents "spontaneously" propagated new audit actions to the UI label map — not because
   they thought of it, but because the type refused to compile until they did. A guarded
   registry stops being a risk and becomes the MECHANISM that carries a new feature to every
   place it must appear.

The hierarchy is deliberate: tier 1 is design (the strongest — makes the problem not
exist), tier 2 is orchestration (catches what design cannot), tier 3 is insurance (catches
what both miss).

**The working habit: one feature = one module.** New functionality is deliberately scoped
inside a single module: its routers, services, repositories, its OWN migration revisions,
its prompts/instruction sets, its tests — plus one appended line per cross-cutting registry.
Where a feature genuinely spans layers (backend + frontend), it spans as ONE mirrored module
per layer (`app/<domain>/<feature>/` ↔ `src/features/<feature>/`), never as edits scattered
across foreign modules. If a "feature" cannot be drawn inside one module per layer, that is
a design signal to stop and re-cut the boundary before building.

**What this buys the method.** Every practice in Part II leans on this chapter: recon can
say "copy module X's shape 1:1" because shapes are module-sized; implementation waves map
onto module ownership; a sealed contract fits on two pages because it describes ONE module's
public surface; reviews and fixes scope by module; and the blast radius of any agent mistake
is a module, not a codebase.

- No dead seams (see Part I).

### Code quality

- **Every file opens with a docstring**: Role / Used by / Depends on / Key invariants. The
  invariants section is the load-bearing part — it is where deliberate decisions live so the
  next agent doesn't "fix" them. A docstring that contradicts the code is itself a defect
  (and cross-references to tests are kept honest by a mechanical guard that resolves every
  `test_*` pointer in docstrings to a real test).
- Files: target 200–400 lines, hard cap 600 — split beyond it (enforced by a ratchet test
  that pins current offenders and refuses new ones or growth).
- Functions: target 30–50 lines, cap 100; nesting ≤ 3, early returns.
- Types mandatory on all signatures; no `Any` without a justifying comment.
- Layers: routes (thin, 15–20 lines) → services (ALL business logic) → repositories (ONLY
  data access) → models. Never skip layers; services never touch sessions directly.
- Barrels: every module exports its public API via `__init__` / `index`; external code
  imports from the barrel only. No circular deps. CRUD naming (`create_/get_/update_/
  delete_`).
- Comments only for: security-critical logic, non-obvious business rules, argued workarounds,
  measured performance decisions. Never "what the next line does", never review notes.
- Custom exception classes only, descriptive names; never expose internals in user-facing
  messages.

### The language boundary (product language ↔ code language)

When the product ships in a language other than English, the boundary is drawn once and
never blurred:

- **Code speaks English** — identifiers, docstrings, commit-adjacent comments, test names.
  Agents reason best in it, and the codebase stays legible to any future engineer.
- **The product speaks the user's language, completely** — every UI string, every generated
  document, every email, every LLM instruction set that produces user-facing text. No
  half-translated surfaces.
- **The bridge is a LABEL MAP with an exhaustiveness guard**: machine values (enum/action
  strings) map to user-language labels through a registry typed `Record<Value, Label>` (or
  sealed by a completeness test), so adding a value BREAKS the build until it is named in
  the product language. Translation can never silently lag the code — the guard carries it.
- **The user's domain word and the code's identifier may differ, deliberately** — record the
  pair once in the decision ledger ("the UI says Преглед; the code and API say
  `consultations`") and keep both stable. Renaming code to chase product vocabulary churns
  everything; renaming product copy to match code identifiers surrenders the user's
  language. The pair costs one ledger line.

### Database

- **Multi-tenancy is the hardest rule in the system**: every tenant table carries
  `org_id NOT NULL`; RLS ENABLE **and FORCE** at the DB with the app connecting as a
  NOBYPASSRLS role and `app.current_tenant` set per request; PLUS explicit org filters in
  repositories (defense in depth); org conditions in JOINs too. No query path without tenant
  scope, ever. AI/serving reads go through the same RLS-bound sessions — permissions are
  enforced BELOW the model, so prompt injection cannot widen access.
- The migration owner MUST hold BYPASSRLS: every cross-tenant path is a SECURITY DEFINER
  function running as the owner, and FORCEd RLS applies to owners too — an owner without
  BYPASSRLS makes those functions return zero rows forever, silently. Assert the attribute
  loudly at startup.
- UUID PKs; `created_at` everywhere; snake_case; JSONB for flexible data with GIN where
  queried.
- Money is NUMERIC, never float. Spend/audit-class tables are INSERT+SELECT only (no
  UPDATE/DELETE grants) — a record you can edit is not a record.
- Append-only audit table: who, what, when, entity, details JSONB, IP — written in the SAME
  transaction as the change it records.
- Migrations: backward-compatible (rolling deploy), run before new containers, never
  destructive without explicit approval; data backfills live with their schema change and
  carry their own frozen copies of seed constants (never import live config into a
  migration).
- Raw SQL only with a justifying comment; repositories own all queries.
- **The cost rule**: every LLM/transcription/embedding call writes a spend row (provider,
  model, units, cost NUMERIC) in the same transaction/task as the work — a Prometheus
  counter is not a spend record (it resets and cannot be reconciled against an invoice). The
  spend table is born WITH its first writer, never earlier.

### Security

- At rest: full-volume encryption on the data volume; offsite backups asymmetrically
  encrypted before leaving. Column-level encryption of searchable personal fields REJECTED
  by design (breaks trigram search/indexes); the risk-based measure set is
  volume+TLS+RLS+access control+audit. Do not re-open without new facts.
- Self-hosted JWT: short access token, refresh in httpOnly cookie, rotation with reuse
  revocation, signing key in secrets, rotatable. WebSocket auth via query-param JWT verified
  against revocation at upgrade.
- Authorization: routes authenticate from the validated JWT; the caller is RE-READ per
  request on admin planes (a blocked org / demoted admin loses the surface NOW, not at token
  expiry). No trusted internal identity headers — the edge strips inbound `X-*` identity as
  hygiene. Every module enforces permissions at its own boundary.
- Brute-force budgets on auth endpoints: per-address AND per-account, atomic charge-then-
  judge, fail-OPEN when the cache is down (a dead Redis must not sign every клиника out),
  hashed keys with TTL (a SCANnable key space is a log line by another door), uniform 429
  that names no budget (no account-existence oracle), counters unlabelled on purpose.
- Uploads: content-type allowlist + declared-size gate BEFORE reading + real-size gate after
  + **decompression/expansion ceiling** for container formats (zip-based files lie about
  their size by ~1000:1).
- Secrets via Docker secrets/env, never committed, never logged, never in `docker inspect`.
  No containers as root. Metrics endpoint token-gated or unreachable from outside;
  `secrets.compare_digest` on bytes.

### GDPR / privacy (generalize to any personal-data domain)

- Privacy is a foundational constraint with a PR checklist: tenant-scoped? in the erasure
  cascade? derived data traceable to source? retention defined? audit-logged if it exposes
  personal data? no personal data to LLM providers outside training-restricted/EU terms?
- **The erasure cascade is designed, not implied**: subject erasure kills the subject's own
  speech (recordings, transcripts, their contact row → anonymized) and all derived data
  (embeddings, memory items with zero remaining evidence, exports), while professional
  records about the subject's property/case survive under their own legal basis — that
  boundary is an owner decision, recorded, with the lawyer named as the open item.
- Consent per category with timestamps, withdraw endpoint, and processing gates that CHECK
  consent state (an email send walks: state gate → address+not-anonymized gate → consent
  gate, in that order, with anonymized and never-gave-address deliberately answering the
  SAME error — the difference is the fact erasure exists to remove).
- Retention: every artifact class has a window and a sweeper; technical logs are capped and
  rotated (they carry pseudonymous IDs = personal data); the audit table has its own rules.
- Human-triggered sends over auto-sends for anything outward (generate → human reviews →
  human clicks send); 202 means accepted, never delivered.

### Observability

- **Three planes, never mixed**: audit (DB, append-only, evidence, same-transaction),
  technical logs (stdout only, JSON per line, rotated, NEVER the database, never evidence),
  metrics (in-memory, `/metrics`, not a record of anything).
- Log format: one JSON object per line; correlation fields (request_id, org/user ids,
  task_id) injected by the FORMATTER from contextvars so no call site can forget them;
  request_id rides queue message headers so worker lines correlate.
- **The never-log list is absolute**: email addresses, names/free text, query strings (reset
  tokens travel there), request/response bodies, auth/cookie headers, passwords/digests, raw
  paths with IDs (use route templates), SQL bound parameters INCLUDING inside tracebacks
  (create every engine with parameter hiding). UUIDs are the allowed pseudonymous currency —
  the UUID→person link lives under RLS where erasure can reach it.
- **The exception-object rule**: on any path where the exception can hold user data (SMTP
  refusals render the address; DB errors render bound parameters; cache errors render
  connection URLs with passwords), log `type(exc).__name__`, never the traceback.
- Argued exceptions are DOCUMENTED and BOUNDED by tests that fail if a second line starts
  carrying an address — an exception you can't enumerate is a leak.
- Metrics labels: route TEMPLATES only (raw paths = unbounded cardinality + tenant IDs), no
  org dimension, security counters deliberately unlabelled. Known multi-process gaps are
  written down as owner calls, not silently "fixed".
- Every access line, one per request, after the response; probes excluded; nothing in the
  observation layer may ever break a request.

### Testing

- One test file per source file, mirrored names; `test_<function>_<scenario>_<expected>` or
  invariant-named for seals.
- **Required tests**: tenant isolation for every data-access function (security
  requirement); 403/404 for every permission-gated operation; happy+validation+auth+
  not-found+isolation for every endpoint; golden-sample tests for every LLM extraction agent
  (fixed input → expected typed output, versioned against prompt+schema+model pin).
- Coverage: 100% on auth/authz/tenancy/audit (CI-gated as a separate pass), high floor on
  the domain, base floor repo-wide; CI fails on decrease.
- Mock external services, LLM APIs, mail, storage, clocks; NEVER mock your own code within a
  module, Pydantic validation, or pure functions.
- Drive the REAL flow: through the ASGI app, real DB, real Redis; "a test that proves the
  FUNCTION but not the WIRING is vacuous" (a service fully tested and dead in the UI for
  months is the canonical lesson). PII log guards capture REAL handler output over real
  flows.
- Fixtures self-contained, transaction-rollback cleanup, descriptive names; no shared
  mutable state; no skipped tests without a ticket.
- Cross-boundary drift guards: when a constant must mirror another surface (frontend limit ↔
  backend ceiling), a test READS the other side's literal from source, so drift fails
  loudly.

### Review & fix-registry

Fully specified in Phases 6–7 above; the rule files carry the same content so agents can be
pointed at them piecemeal (lenses → adjudication → fixes; claims never ground truth; severity
rubric; canary calibration for dry-exit loops; ceilings; the clean full gate; the two-artifact
fix rule and the committed/uncommitted scope rule).

---

## Part IV — Orchestration mechanics (the harness craft)

The operational knowledge that makes multi-agent runs actually work.

### Model tiering

- **Judging roles get the strongest model**: sealed-oracle author, adjudication of findings,
  cross-vendor reconciliation. The oracle IS the product of those phases.
- **Implementation fleets run one tier down** (Opus-class): 3–6 builders in dependency
  waves.
- **Two axes of decorrelation**: another model tier for the judge (Fable judges Opus), and
  another VENDOR for review (GPT/Codex reviews Claude's work). Different families, different
  blind spots — a cross-vendor finding you missed is real signal, and its wrongness modes
  are also different (verify everything).

### Workflow design

- Prefer pipelines to barriers; a barrier is justified only by genuine cross-item
  dependency (dedup across all findings before expensive verification).
- Waves by file ownership: parallel agents receive disjoint file sets and explicit "do NOT
  touch X (another agent owns it)" lines. Sequential where they share barrels/registries.
- Every build agent's prompt carries: the spec/contract path, the house-rules pointer, the
  environment block (DB/Redis ports, encoding), "targeted tests only, never two suite runs
  against the shared DB", lint/format on touched files, "report: built, files touched,
  tests run with numbers, deviations with reasons".
- Agents die mid-flight: resume with an explicit "you inherit partial work — audit it,
  don't restart" note (and the successor honestly reports what it authored vs audited).
  Workflows support resume-from-run-id with cached prefixes; read the journal before
  assuming a cached result exists.
- Reviewer/adjudicator agents are READ-ONLY; only the fix stage edits. Fix agents receive
  findings WITH verification evidence, never bare claims.

### Long-running gates and stalls

- Long test runs go to background with output redirected to a durable log ending in a DONE
  marker. The orchestrator arms a **watchdog monitor**: emits an event per completed log AND
  a stall alarm when no test process has been alive for N minutes while logs are incomplete.
- Background agents STALL between steps (completion notifications get lost). The cure is a
  nudge message to the same agent ("step (b) finished — its log says DONE; proceed to (c)
  now; if a notification doesn't arrive within 5 minutes, read the log yourself and
  continue"). Design agent instructions to self-check logs rather than sleep on
  notifications.
- Probe databases are created per gate and dropped after; leftover probes and logs are
  cleaned by whoever finds them.
- The orchestrator never predicts a pending agent's results, never treats its own monitor
  events as user input, and reports interim state honestly ("still running, ETA ~X").

### Communication discipline (orchestrator ↔ human)

- Lead with the outcome; numbers in tables; findings with file:line; refutations reported
  with as much pride as fixes.
- Deviations from the agreed plan are surfaced explicitly with the reason (e.g. "I
  recommended per-round commits; at commit time the rounds' files overlap, per-round history
  would create untested intermediate states — one commit of the gated whole, here's why").
- When the human reports a bug: diagnose with evidence first (Phase 1 style), because the
  report is a claim about symptoms, not causes.
- When an experiment is run (like sealed tests), report it AS an experiment: what was
  measured (rounds to green, findings avoided, token cost, whether anyone touched the seal),
  what the verdict is, and where the method does and doesn't fit.

---

## Part V — Quick-start checklist for a new project

1. Create `.claude/rules/` with the nine rule files (architecture, code-quality, database,
   security, gdpr/privacy, observability, testing, review, fix-registry) — adapt Part III.
2. Create `CLAUDE.md` as the decision ledger: vision, settled decisions with dates, build
   order, the "never commit/push without explicit instruction" rule, pointers to the rules.
3. Create `docs/FIX-REGISTRY.md` with the table header and the two-artifact rule.
4. Wire CI: lint+format repo-wide, full suite, coverage gates (100% on auth/authz/audit
   class modules, domain floor), boot-without-domain-module test.
5. Establish the module map (core vs domains) and the "one feature = one module" habit.
6. For the first substantive feature: Phase 0 design → Phase 1 recon → decide sealed-oracle
   vs classic path → walk the ladder.
7. Set up the live-testing scenario folder (gitignored) the day real users first touch it.
8. First audit before first release: loop-until-dry WITH canaries, ceilings declared.

---

*Compiled 10.08.2026 from the working sessions of a production engagement; every practice
here ran at least once for real before being written down.*
