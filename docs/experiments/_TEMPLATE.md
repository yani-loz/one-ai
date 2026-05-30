---
id: EXP-XXX
title: <short descriptive title>
date_started: YYYY-MM-DD
date_concluded:
status: running          # running | concluded | inconclusive | paused
tags: []                 # e.g. [retrieval, models]
related: []              # other EXP ids this builds on or contradicts
---

# EXP-XXX — <Title>

## 1. Question
<The single question this experiment answers. One sentence. If you can't state it in one sentence, it's two experiments.>

## 2. Hypothesis
<What you expect to happen, and *why*. Write this BEFORE running — it lets you be honestly wrong instead of rationalizing afterward.>

## 3. Why it matters
<Which build decision depends on the answer. If nothing depends on it, don't run the experiment.>

## 4. Setup / Method
<Exactly what was done — enough that you (or an AI agent) could reproduce it cold. Models + versions, dataset/sample, parameters, prompts, code/commit refs, commands. Link files rather than pasting long blocks.>

## 5. Results
<Raw observations only — numbers, tables, sample outputs, screenshots. Facts, no interpretation yet. This is the part you'll be glad you wrote down.>

## 6. Analysis
<What the results mean. Surprises. Confounders or things that could invalidate it. What you'd trust vs. what needs another pass.>

## 7. Decision
<The conclusion stated as a directive — what we now believe and what changes in the build because of it.>

→ **If this settles a question, copy this line into `NOTEBOOK.md` → Settled Decisions.**

## 8. Follow-ups
<New questions raised, next experiments to run, open threads. Add the good ones to the notebook's Open Questions.>

---
*Minimum viable experiment = sections 1 (Question), 5 (Results), 7 (Decision). The rest are optional for quick tests but pay off the moment an experiment turns out to matter.*
