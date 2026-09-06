# ASK-01 — Ask-layer architecture (tools + agent), as built and measured

**Status:** Phase-1 BUILT · optimization loop BLOCKED-on-decision (2026-07-05) · reader arm decision pending
**Status as of 2026-09-06:** branch `ask-tools-loop` is **5 commits** ahead of `main` (`5463aef`, `bda63de`,
`d564df0`, `e4a4535`, `1feb172` = HEAD) and has **no upstream** — `origin` exists but carries no
`ask-tools-loop` ref (`git branch -vv`, `git branch -r`). Most of the layer described below lives only in
the uncommitted working tree (see §9). No HTTP or MCP route reaches it; it is exercised only by
`backend/scripts/ask_loop`.
**Evidence of record:** `Benchmarks/_ask_loop/ledger.md` (outside repo — PII) · branch `ask-tools-loop`
(5 commits, never pushed)

## 1. Position

The Ask layer answers natural-language questions over Connect's memory. A small reader LLM runs a
bounded tool-calling loop; every tool executes on the PF-01 **reader plane**, so the agent
physically cannot read outside the asking person's grants nor write anything. This document is the
end-state architecture after 14 measured mutations, 2 architecture arms, and 3 verifier audits.

## 2. Security architecture (non-negotiable substrate)

```
question ──▶ AskAgentRunner ──▶ ToolRegistry.dispatch ──▶ reader_session(org_id, person_id)
                                                              │  role oneai_reader: SELECT-only,
                                                              │  NO BYPASSRLS
                                                              ├─ org RLS (app.current_org_id GUC)
                                                              └─ PF-01 visibility policies
                                                                 (app.current_person_id GUC,
                                                                  acl_grant, fail-closed)
```

- `person_id` comes ONLY from the verified auth binding (`principal_source_identity`, AC20).
- Tools contain **zero tenant logic** — scope is enforced server-side; a compromised prompt
  cannot widen it. Measured: unbound person ⇒ 0 rows visible; fabricated citations ⇒ 0 across
  ~600 graded episodes (the mechanical invented-citation gate never fired on real evidence).
- Derived-layer objects MUST be `security_invoker = true` views (a plain view executes as owner
  and silently bypasses RLS) — see migrations 0020/0021/0022 (`0022_counterparty_summary_v3` is the
  applied head on the dev DB; migration state as of 2026-09-06 in §9).

## 3. The agent (flat monolith — the measured winner)

`app/ask/services/agent_runner.py` — **AskAgentRunner**:
- Bounded loop: ≤ `ask_max_tool_turns` (8) model turns with tools; cap forces a tools-off final
  answer, graded as-is. Turn raise to 12 measured: no gain (the model doesn't use extra turns).
- **Citation contract** (accepted, MUT5): every factual statement must carry `[id: <uuid>]` from
  tool payloads; id-less answers are declared invalid in the prompt. Corollary (standing rule):
  **every tool payload must carry citable record ids** — an id-less payload forces the model to
  fabricate citations on otherwise-correct answers (measured, MUT11/11b).
- Scaffold guards: `max_tokens=4096` default (Together's Qwen reasoning channel spends completion
  tokens BEFORE content; 1024 truncates to empty answers) + one-nudge on empty finals. A stronger
  degenerate-answer recovery (tool-call-as-content, repetition loops) is proven to eliminate the
  artifact class (0/27 episodes) — diff preserved at `_ask_loop/mutations/MUT14_degenerate_guard.diff`,
  promote with the production build.
- System prompt is corpus-agnostic (anti-bias rule, verifier-audited): grounding+citation rules,
  no-data honesty, ambiguity-candidates, count-via-tools, generic multilingual retry guidance.

## 4. Tool layer (accepted set = 6 generic primitives)

`app/ask/tools/registry.py` — ToolSpec (name, description, JSON-schema params, executor) +
ToolRegistry (LLM serialization, safe dispatch; tool errors become repairable observations).

`app/ask/tools/shared_core.py` — assembles the accepted set from the per-domain modules
(`person_tool`, `email_search`, `email_read`, `attachment_tools`, sharing `email_filters` +
`tool_helpers`); each tool's ToolSpec lives beside its executor:

| Tool | Contract highlights |
|---|---|
| `search_emails` | `queries[]` ≤5 variants, OR semantics (bilingual by construction); envelope: `total_matches`, `per_term_matches`, subject-hit-first ranking, narrow/translate hints |
| `count_emails` | same filters; counts computed server-side (model must never hand-count) |
| `get_email` | full body + recipients + attachment metadata by id |
| `find_person` | name/address contains-match over person + person_email; returns all bound addresses |
| `search_attachments` | filename/extracted-text search with carrier-email context |
| `get_attachment` | extracted document text of ONE attachment by id, paged via `next_offset` (`attachment_tools.py`, same module as `search_attachments`) |

Standing rules (all verifier-audited): no benchmark/corpus literals anywhere; LIMITs capped
server-side; compact payloads (snippets, ISO dates); citable ids in every payload.

**Tool-count fragility (measured):** at 9B scale, each added tool degraded marginal questions more
than it unlocked (MUT3/6/8/12 all rejected on this pattern). The counterparty-summary tool
(`get_counterparty_summary` over the dossier view) was accepted then rescinded when the verifier
proved its delta was mostly grader artifacts; the view itself (migrations 0020/0021, superseded by
`0022_counterparty_summary_v3`; `counterparty_summary` with citable `first/last_message_id`) remains
applied and re-proposable — its Q004 mechanism (first-contact one-hop) was genuinely verified. As of
2026-09-06 no tool exposes the view: it is reachable only through the generated-SQL hatch, which is
why its `security_invoker` property is load-bearing (`0022_counterparty_summary_v3.py` docstring).

## 5. Agent hierarchy: router + specialists (BUILT, MEASURED, PARKED)

`app/ask/services/router.py` + `docs/PM/ask/intent-classes.md`:
- 6 universal intent classes (taxonomy-derived, corpus-agnostic): `entity_lookup`,
  `content_search`, `aggregation`, `temporal_activity`, `synthesis`, `existence_check`.
- One cheap classification call (same pinned model; parse content first, else LAST class named in
  reasoning); any failure → generalist fallback (full registry, no addendum). Router never blocks.
- Per-class kits: tool subset (remove-only from shared registry) + procedure block appended to the
  system prompt.
- **Measured verdict (N=3 full-dev):** flat 24.2% vs routed 18.2% — the specialist prompts
  destabilized the 9B's output format (3–5 malformed answers/rep) more than class focus gained;
  router standalone accuracy 70%. **Parked; rematch precondition: a stronger reader arm.**
  Harness support stays (`run_eval --arm routed`).

## 6. Adapter boundary

`app/ask/adapters/together_chat.py` — the ONLY file that knows the LLM wire format
(OpenAI-compatible chat completions; bounded retries; cumulative usage; key never logged).
Model identity pinned in settings (`Qwen/Qwen3.5-9B`); params mutable, model change = new arm.
Provider swap = new adapter, nothing else moves.
As of 2026-09-06 the pin is unchanged: `ask_reader_model` = `Qwen/Qwen3.5-9B` in `backend/app/core/config.py:137`
(identical in the working tree and at HEAD `1feb172`), read once at `together_chat.py:48` and NOT overridable
per call (`:76`); no `ASK_READER_MODEL` override exists anywhere in the repo. `ASK-02-small-model-to-100-safe.md`
declares `google/gemma-4-31B-it` as the reader arm — that arm was never made the checked-in default.

## 7. Measurement architecture (CI steps written but uncommitted — never yet run)

`scripts/ask_loop/`: `run_eval` (per-question reader-plane runs, answer cache keyed by
config-hash INCLUDING source code of tools/runner/router — silent-staleness lesson learned 3×),
`grade` (typed deterministic tiers: count/date/entity/list/no-data/ambiguity + mechanical
invented-citation gate; free-text residue → isolated Opus critic, claim-by-claim binary
entailment), `conformance` (16 pinned cases incl. every historical grader bug — run before/after
any grader change). **N=3 repeats, majority vote per question** (Together serving is
non-deterministic at temp 0 — measured 3/12 same-config flips). Independent Opus verifier
(`.claude/agents/ask-tools-verifier.md`) audits every accept + checkpoint; it reversed one accept.

**CI status as of 2026-09-06:** the three gate steps exist — `.github/workflows/ci.yml` runs
`scripts.ask_loop.conformance`, `scripts.ask_loop.seal_check` and `scripts.ask_loop.defence_matrix` after
`pytest` — but only as an **uncommitted working-tree edit**: `git show HEAD:.github/workflows/ci.yml`
contains none of the three, the workflow triggers on `push: [main]` + `pull_request`, and `ask-tools-loop`
has never been pushed. So they have never run in CI on any branch. Five of the harness modules behind them
(`seal_check.py`, `defence_matrix.py`, `conformance_cases.py`, `conformance_golds.py`,
`answer_extraction.py`) are themselves untracked — see §9.

## 8. Measured state (2026-07-05, honest numbers)

Baseline 12.1% → **accepted MUT5 24.2% dev / 23.1% holdout** · gap −1.9pp (no overfitting;
holdout = unseen entities) · fabrication 0 · novel-entity probes 90% (simple shapes) ·
plateau counter 10/20. Remaining failure clusters are reader-capability-bound (deep multi-claim
synthesis, per-candidate verification, cross-script identity). Decision pending (founder):
stronger reader arm (recommended) / grind to plateau / accept as Phase-1.

**Superseded as of 2026-09-06** — the numbers above stand as the honest record *of 2026-07-05*, but the
campaign continued past them (Gemma-4-31B arm, then CKPT2 on questions_v2). For the current scoreboard read
`docs/experiments/EXP-002_ask-tools-loop-diary.md` §7.10; for what may and may not be quoted read
`docs/PM/ask/ASK-02-overnight-analysis-2026-07-07.md` §1 and §4.3 — the honest single-roll number is
**27/43 (62.8%)** and 33/43 (76.7%) is a best-of-N union that is **reproducible in no single roll: do not
quote it**. Those later numbers were measured on the Gemma arm; the checked-in reader default is still
`Qwen/Qwen3.5-9B` (`backend/app/core/config.py:137`, working tree and HEAD — see §6), so the founder's
reader-arm decision is still open.

## 9. File map

```
backend/app/ask/                     the production layer
  adapters/together_chat.py          vendor boundary
  services/agent_runner.py           bounded agent loop (flat trunk) + _fit_payload budgeting
  services/router.py                 router+kits (parked arm)
  services/sql_pipeline.py           direct-SQL answer pipeline (run_eval --arm xiyan-routed)
  tools/registry.py                  tool contract + savepoint-contained dispatch
  tools/shared_core.py               registry assembly (the accepted tool set — 41 lines)
  tools/{person_tool,email_search,email_read,attachment_tools}.py   executors + their specs
  tools/{email_filters,tool_helpers}.py    shared WHERE clause + arg primitives
  tools/{sql_tool,sql_guard}.py      generated-SQL hatch + its fail-closed validator
  tools/sql_execution.py             the ONE path generated SQL takes to the DB: validate, snapshot
                                     the org/person scope, execute, prove the scope did not move
  tools/sql_provenance.py            did each returned value come from the DATABASE or the caller —
                                     the anti-fabrication check over the EXPLAIN plan + rows
  exceptions.py
backend/tests/ask/                   the layer's test surface
  conftest.py
  tools/          12 modules: sql_guard, sql_execution, sql_hatch_isolation, reader_seam,
                  read_tools_isolation, person_and_isolation, email_search, lexer_alignment,
                  plan_scanner, provenance, result_bounds, registry_dispatch
  security/       attack_corpus + fabrication_corpus + test_attack_corpus + test_ledger
  services/       agent_runner, router, cache_identity, harness_integrity
  adapters/       together_chat
backend/scripts/ask_loop/            eval harness: run_eval, grade, conformance (+conformance_cases,
                                     conformance_golds), answer_extraction, seal_check, defence_matrix
backend/scripts/backfill_email_grants.py   PF-01 grant backfill (re-runnable)
backend/app/db/migrations/0020,0021,0022   counterparty_summary view (security_invoker)
backend/app/db/migrations/0023             reader BCC + seen-window RESTRICTIVE policies
docs/PM/ask/intent-classes.md        router taxonomy contract
docs/PM/ask/ASK-SECURITY-LEDGER.md   per-finding security seals (what seal_check executes)
Benchmarks/_ask_loop/                ledger, runs, gold, verifier reports (PII — never in git)
```

**Migration state as of 2026-09-06** — measured on the dev DB `one-ai-mvp-db-1`
(`docs/audits/2026-09-06_built-vs-docs-map.md` §3): `alembic_version` = **`0022_counterparty_summary_v3`**,
so 0022 is the applied head. **`0023_reader_bcc_and_seen_window` is untracked in git and UNAPPLIED** — the
BCC and seen-window restrictions it defines are not in force on that database.

**Git state as of 2026-09-06 — most of the tree above exists in no git object.** Untracked (`??`):
`attachment_tools.py`, `email_filters.py`, `email_read.py`, `email_search.py`, `person_tool.py`,
`sql_execution.py`, `sql_provenance.py`, `tool_helpers.py`; migration 0023; the five `ask_loop` modules
`answer_extraction`, `conformance_cases`, `conformance_golds`, `seal_check`, `defence_matrix`; and all of
`backend/tests/ask/` except four intent-to-add stubs. Intent-to-add with an **empty blob**
(`git ls-files --stage` → `e69de29b`, i.e. zero committed bytes): migration 0022 and this document.
Modified-but-uncommitted: `together_chat.py`, `agent_runner.py`, `router.py`, `sql_pipeline.py`,
`registry.py`, `shared_core.py`, `sql_guard.py`, `sql_tool.py`, `run_eval.py`, `grade.py`,
`conformance.py`. Nothing is staged and the branch has no upstream.
