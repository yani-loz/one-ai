# Experiments — How This Folder Works

This is the **lab notebook** for the One AI MVP build. When we don't know something — which model, which retrieval approach, which chunking strategy, whether an idea even works — we run an experiment and record it here instead of deciding from intuition.

The discipline is simple: **log continuously, then graduate settled conclusions into the build spec.** Experiments produce *knowledge*, and undocumented knowledge evaporates within days. This folder is how we keep it.

---

## The files

| File | What it is | When you touch it |
|------|-----------|-------------------|
| **`README.md`** | This file — how the system works (the manual). | Rarely. |
| **`NOTEBOOK.md`** | The live ledger: settled decisions, open questions, and the index of every experiment. | Constantly — every time you start/finish an experiment. |
| **`_TEMPLATE.md`** | The blank structure for a single experiment. Copy it to start one. | When starting a new experiment. |
| **`EXP-XXX_slug.md`** | One file per experiment — the full record (question → method → results → decision). | While running and concluding that experiment. |

**README vs NOTEBOOK:** the README is the *manual* (how the system works); the NOTEBOOK is the *ledger* (what's actually been tried and concluded). When in doubt, conclusions go in the NOTEBOOK, process explanation stays here.

---

## The workflow

1. **A question comes up** → add it to **Open Questions** in `NOTEBOOK.md` (or, if you're acting now, skip straight to step 2). If no build decision depends on the answer, don't run it.
2. **Start the experiment** → copy `_TEMPLATE.md` to `EXP-XXX_<slug>.md`, fill in the Question, Hypothesis, and Why-it-matters *before* running. Add a row to the **Experiment Log** in `NOTEBOOK.md` with status 🟡.
3. **Run it** → record raw observations in the **Results** section as you go. Facts only, no interpretation yet.
4. **Conclude** → fill in Analysis + Decision. Update the notebook row's status (✅ / ❌) and one-line outcome.
5. **Graduate** → if the experiment settled a question, copy its one-line Decision into **Settled Decisions** in `NOTEBOOK.md`. That table is what feeds the build spec and the code.

```
EXP file (depth)  →  NOTEBOOK Settled Decisions (conclusions)  →  build spec / code
```

---

## Conventions

- **Naming:** `EXP-XXX_short-slug.md` — zero-padded 3-digit ID, kebab-case slug (e.g. `EXP-007_hybrid-vs-pure-vector.md`). IDs are sequential and **never reused** — an abandoned experiment leaves a visible gap, which is itself information.
- **Status:** 🟡 running · ✅ concluded · ❌ inconclusive / dead end · ⏸ paused.
- **Tags** (reuse where possible): `retrieval`, `models`, `cost`, `connectors`, `memory`, `agent`, `frontend`, `infra`, `eval`.
- **Minimum viable experiment:** even a 5-minute test gets a file. Mandatory sections are **Question → Results → Decision**; the rest of the template is optional for small tests. Don't let the format become paperwork — the goal is to never lose a finding, not to write essays.

---

## Principles

1. **Write the hypothesis before you run.** It lets you be honestly wrong instead of rationalizing after the fact.
2. **Capture the reasoning, not just the result.** *Why* you decided is the first thing memory loses and the most valuable thing to keep.
3. **Don't test what doesn't matter.** Every experiment should resolve a real build decision.
4. **State decisions as directives.** "Use X because Y" — not "X seemed better." The spec needs instructions, not impressions.

---

## Related

- **`../Project_Bible.md`** — the full project context (what One AI is and how it's architected). Experiments resolve the *open* questions the Bible doesn't yet answer.
- The build spec (when written) pulls its technical decisions from **Settled Decisions** in `NOTEBOOK.md`.
