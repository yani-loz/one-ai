# Code quality rules

## When to read this
Working on ANY code in this repo. Applies to BE (Python) AND FE (TypeScript). Auto-loaded into every session.

---

These rules are self-contained. There is no companion `docs/code-style/` tree — it has never existed on any branch (verified 2026-09-06: `ls` exits 2, `git log --all -- docs/code-style` is empty), so nothing here defers to a longer document.

---

## A1 — Naming as retrieval interface

- **YOU MUST use descriptive names.** No `f()`, `g()`, `do_thing()`, `helper()`, `temp()`, or single-letter names except loop indices (`i`, `j`, `k`) and standard short-form (`e` for exception, `_` for unused).
- Functions/methods: verb + noun describing the action (`compute_dedup_key`, `validate_generated_sql`, `project_email_for_non_owner`).
- Classes: noun describing what it represents (`ParsedEmail`, `ToolRegistry`, not `Manager` / `Handler` / `Processor`).
- Variables: domain-meaningful (`visibility_scope`, not `v` or `flag1`).
- CSS classes: domain-prefixed (`text-brand-gradient` — the one hand-authored class in `frontend/src/index.css`; everything else is Tailwind utilities), not `.b1` / `.x` / `.container2`.

**Test:** can a human grep your function name and find it from a feature description? If no, rename.

---

## A2 — File size & modularity

A 666-line file is a tax Claude pays on every turn that touches any of it. **Modularity is now an economics decision, not just hygiene.**

- **IMPORTANT: Hard ceiling — 500 lines.** CI fails any file ≥500 lines. No exceptions.
- **Soft target — under 300 lines.** Files at or below this stay sharp for AI editors.
- **WARN at 300 lines.** Pre-commit hook surfaces a warning; treat it as "split soon" not "argue later."
- One responsibility per file. If you describe the file's purpose using "and" (`auth and user management`), split it.
- Splitting target: each file describes ONE domain concept (`sql_guard.py`, not `validators_and_helpers.py`).
- The code should be split in self conteined modules when possinble independent so when we work with claude code on a module it to be more concentraited.

---

## A3 — Directory structure as map

A flat `src/` with 200 files forces Claude to Glob across everything. A domain-shaped tree lets Claude guess the location from the path **before any search runs**. Saves 5-10 Glob calls per session.

- **Group by domain, NOT by type.** Use `backend/app/connectors/imap/`, `backend/app/access/`, `backend/app/ask/` — not `backend/app/services/` with 50 mixed files, not `backend/app/all_email_and_access_helpers/`.
- Per-domain folders contain the layer split (routes/services/repositories/models or whatever the layer convention is in that scope).
- New module = new domain folder, not a new file in the flat list.
- **Path is a clue.** If reading the path doesn't tell you what's inside, the path is wrong.

---

## A4 — Documentation as runtime input

Docs and types are **runtime input the model uses every turn**, not future-dev courtesy. CLAUDE.md is prepended to the system prompt; types and docstrings are read every time the file is read. Three compounding levels:

- **YOU MUST add a file-level docstring at the top of every file.** Sections: **Role** (what this module does), **Used by** (which modules depend on it), **Depends on** (what it imports from inside the project), **Key invariants** (rules this module owns that callers should not violate). Without this, Claude wastes a turn reading the file to figure out whether it's even the right file.
  - **Trivial-file exception (accepted convention):** empty `__init__.py` package markers and trivial unit-test modules may use a single concise one-line docstring instead of the 4-section header — the 4 sections would be boilerplate noise on files with no real content. Test modules with a non-obvious dependency (e.g. they need a live DB) still get a fuller docstring explaining it.
- **Function-level docstrings on every public function.** Python: Google-style. TypeScript: JSDoc. Sections: purpose, contract, edge cases. Skip on private helpers if the function name + types already convey the contract.
- **Types on every function signature.** Python: type hints (no `Any` without a justifying comment). TypeScript: strict mode, no `unknown`/`any` without a comment.
- **The 80% rule:** if the file-level docstring + types + function docstring don't explain what the file does to a stranger, it's underdocumented. Add prose.

---

## A5 — SOLID / Separation of concerns

30-year-old principles. The new beneficiary is the AI: SoC means Claude reads fewer files per turn, which means fewer accidental edits to code that wasn't supposed to change.

- **Single Responsibility:** one reason for a class/function to exist. A class doing `calculation + persistence + notification` is three classes.
- **Layer architecture (enforced):** routes → services → repositories → models. Never skip layers. Routes parse + return (≤20 lines). Services hold business logic. Repositories do data access only. No business decisions inside repositories.
  - *Known divergence (2026-09-06):* `backend/app/ask/` has no `repositories/` package — its services and tools compose SQL directly (`app/ask/tools/email_search.py`, `app/ask/tools/sql_execution.py`), unlike `access`, `connectors`, `connectors/imap`, `entities` and `identity`, which all have one. **Whether this is a sanctioned exception or a debt to repay is an open founder decision — this rule does not rule on it.** Do not cite `app/ask/` as precedent until it is settled.
- **Loose coupling:** depend on interfaces (abstractions), not concrete classes. Use dependency injection (FastAPI `Depends`, TypeScript constructor injection) instead of imports.
- **Custom exceptions only** — never `raise Exception(...)`. Use descriptive names: `TenantContextMissingError`, `ConnectorConfigurationError`, `DuplicateConnectionError`.
- **No dead code, no commented-out code, no `TODO` without a tracked ticket reference** (`# TODO(ONEAI-42): ...`).

---

## Quick reference

| Rule | Concrete limit |
|---|---|
| File size | <300 target / <500 hard (CI fails) |
| Function size | ≤50 target / ≤100 hard |
| Function nesting | max 3 levels — use early returns |
| Module docstring | required (Role / Used by / Depends on / Key invariants) |
| Public function docstring | required |
| Type hints / TypeScript types | required on all signatures |
| `Any` / `unknown` | requires justifying comment |
| Custom exceptions | required (no `raise Exception`) |
| Naming | descriptive verb+noun for functions; domain nouns for classes |
| Layer skipping | forbidden (routes → services → repositories → models) |

