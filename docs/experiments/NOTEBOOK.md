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
| *D1* | *(example — delete) Use hybrid BM25+vector retrieval; pure vector misses exact terms.* | *EXP-002* | *2026-06-01* |

---

## ❓ Open Questions / Backlog

*Things worth testing, not yet started. Each line: the question + why it matters (what build decision depends on it). If nothing depends on the answer, don't test it.*

- [ ] *(example — delete) Which embedding model gives best recall/€ on our doc mix? → picks the production embedder.*

---

## 🧪 Experiment Log

*Newest first. One row per experiment. Keep the hypothesis and outcome to one line each — detail lives in the linked file.*

| ID | Date | Title | Hypothesis (one line) | Status | Outcome (one line) |
|----|------|-------|-----------------------|--------|--------------------|
| *[EXP-001](EXP-001_example.md)* | *2026-05-30* | *(example — delete)* | *X will outperform Y because…* | *🟡* | *—* |

---

## Conventions

- **File naming:** `EXP-XXX_short-slug.md` — zero-padded 3-digit ID, kebab-case slug. IDs are sequential and never reused (even if an experiment is abandoned).
- **Status legend:** 🟡 running · ✅ concluded · ❌ inconclusive / dead end · ⏸ paused
- **Tags** (free-form, reuse where possible): `retrieval`, `models`, `cost`, `connectors`, `memory`, `agent`, `frontend`, `infra`, `eval`.
- **Minimum viable entry:** even a 5-minute test gets a file. The mandatory sections are **Question → Results → Decision**; everything else in the template is optional for small experiments.
