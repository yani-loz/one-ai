---
name: hardening-loop
description: Set up a multi-round adversarial hardening campaign that provably CONVERGES — adversarial agents hunt, findings get fixed, and each fix is sealed by a test that fails when the fix is reverted. Emits a ready-to-paste /goal instead of starting, so the loop runs to completion unattended. Use when asked to "harden", "keep finding and fixing until nothing critical remains", run repeated red-team passes, or stop a fix loop from going in circles.
disable-model-invocation: true
---

Set up a hardening loop for: **$ARGUMENTS**

## THIS SKILL DOES NOT START THE WORK

It **orients, confirms with the operator, and emits a `/goal` text.** Then it stops. The operator
pastes the goal and the loop runs to completion under the goal evaluator.

Why: a hardening campaign is many hours across many turns. `/goal` is the mechanism that keeps it
running and — critically — **judges completion with a fresh model instead of the one doing the
work**. An agent that has been fixing for six hours is the worst possible judge of whether it is
done. Do not skip to the work.

## The problem this solves

Fix loops go in circles because **a green test proves the payload never reached the caller — it
does not prove WHICH layer stopped it.** So a fix can be deleted, or shadowed by a check that
fires earlier, with the whole suite still green. Then the next round re-reports the same ground.

The distinction that makes a loop terminate:

| | proves |
|---|---|
| **outcome pin** | the payload never reached the caller |
| **causal pin** | with the neighbouring layers neutralised, THIS defence rejects |

A campaign that only has outcome pins cannot converge. Build both.

## Procedure

### 0 — Orient (read only, ~5 tool calls, no fixing)

- The target's surface, and `.claude/rules/*` for the standards it is judged against.
- **Any existing ledger of closed findings** (`docs/PM/**/*LEDGER*.md` or similar) — a campaign
  that re-litigates closed ground is the waste this skill exists to prevent.
- What verification already exists: test command, lint, size gate, any mutation/seal script.
- What the DB/service topology is, and **which ports must not be touched**.

### 1 — Confirm with the operator (do not proceed silently)

State back, briefly:
- **Scope**: what will be attacked, and what is explicitly out.
- **Artifacts present vs missing**: ledger? seal runner? mutation matrix? attack corpus? Name which
  must be BUILT as part of the campaign — that is often most of the first round's work.
- **The termination condition** you will encode.
- **Anything that would make the goal unverifiable** — e.g. no runnable test command.

### 2 — Emit the `/goal` text

The evaluator **does not run commands and does not read files**. It judges only what the working
model has surfaced in the conversation. So the condition must name the commands whose **output
must appear in the transcript**, and the exact strings that count as proof.

Use `assets/GOAL_TEMPLATE.md`. Fill every `<...>`. Keep it under 4000 characters.

A goal that will FAIL to converge:
> ~~"no critical or medium issues remain"~~ — nothing in the transcript can demonstrate it.

A goal that converges:
> "…and the transcript shows `seal_check` output reading `0 broken · 0 with no runnable pin`,
> and `defence_matrix` reading `every claim proven`, both from runs made AFTER the last edit."

### 3 — Stop

Print the goal in a copyable block. Say plainly that you are not starting. Do not begin the loop
even if the next message looks like approval — the operator sets the goal.

---

## The mechanism the goal will drive

Four artifacts. If they do not exist, building them is round 1.

1. **Ledger** (`assets/LEDGER_TEMPLATE.md`) — every confirmed finding, what closed it, and the
   executable pin. The hand-off between rounds: a new pass is given the CLOSED list and told to go
   past it.
2. **Seal runner** (`assets/seal_check.py`) — EXECUTES every pin the ledger names and fails naming
   any finding whose seal gave way. Exit code is the product.
3. **Mutation matrix** (`assets/mutation_matrix.py`) — disables ONE mechanism at a time and
   re-runs the corpus. This is what produces causal pins.
4. **Attack corpus as DATA** — cases as a list, not as test functions, so coverage cannot be
   quietly dropped by a refactor. Plus an ALLOWED corpus: over-rejection is the other failure.

## Rules

### A — When a finding is closed
1. Closed = **a named test FAILS if the fix is reverted.** Not "the code looks fixed."
2. **Prove it, don't assert it**: revert → watch it fail → restore. If it doesn't fail, it is not a pin.
3. Every pin is **executable**. A line in a document is not a pin.
4. A row with **no runnable pin is NOT closed** — and it fails the build, not warns.

### B — Causality
5. Every mechanism declares a **causal claim**: a case that MUST get through when that one
   mechanism is off.
6. A mechanism with no such case is **unproven**, whatever the ledger says.
7. A case that gets through with **everything on** proves nothing — it is an escape.
8. Disable at a **flag the check itself reads**. Swapping a structure the code never consults is a
   silent no-op that reports redundancy which does not exist.
9. **Verify the mutation AND the restore landed.** A missed restore leaves every later row running
   with the rule still off.
10. A mechanism that blocks nothing alone must **say so in writing**, not by being absent.

### C — Agents
11. Adversarial / verify / judge agents are **Opus, set explicitly**.
12. Agents **do not run the test suite, do not touch the DB, do not edit files.** Shared state gets
    corrupted and the results look exactly like a real regression.
13. Hand them the **ledger of closed findings** so they go past it.
14. Every claim is verified personally: **CONFIRMED / QUALIFIED / REFUTED** with evidence.
15. Ask explicitly for **"what I could NOT break, and why"** — half the value, and the harder half
    to write.

### D — Corpus
16. Cases are **data**, with **provenance** and **which guarantee** each protects.
17. A case **never encodes the mechanism** that stops it — a redesign must still satisfy it.
18. An **ALLOWED corpus is mandatory**. Hardening that stops answering real questions is a regression.
19. A case that **cannot fail** in the harness is not coverage (an empty fixture cannot demonstrate
    a volume/cardinality property).

### E — Measurement
20. Test the **machinery**, not only the verdicts — a corrupted regex passes every behavioural test
    while the layer it drives is off.
21. **Positive control** on every cross-tenant test: assert the other tenant DOES see its own row.
22. Counters need a **floor** — `N/N PASS` passes just as loudly with cases deleted.

### F — Hard prohibitions
23. **Never two jobs against one database at once.** The failure signature is indistinguishable
    from a real isolation regression.
24. **Do not edit while a long verification runs** — you invalidate it and pay the wall-clock twice.
25. **Documentation is never a boundary** — not a schema card, not a docstring, not a parameter name.
26. **Do not decide product questions for the founder.** Where the fix costs real capability, record
    it as a decision with the cost priced, not as "fixed" and not as a caveat.

## Traps that cost real time

- **A rename is not a constraint.** Renaming a parameter to `disclosed_addresses` does not stop a
  second caller passing the wrong thing. Share a constant; add a test over the second caller.
- **The second-caller pattern.** After hardening a path, grep for *every other* caller of it —
  scripts, backfills, admin paths. A second writer with a different rule on one shared surface
  oscillates.
- **Move the rule to where the planes converge.** A per-row rule enforced in application code is a
  rule the *next* plane will not have. In a DB-backed system that means RLS.
- Prefer **allowlists**: a denylist over an enum or a function catalog admits everything nobody
  thought of.
- **Shell heredocs mangle backslashes.** Write regexes/escapes with the Write or Edit tool, never
  through a heredoc — a `\b` became a literal backspace and silently disabled a whole layer.

## Assets

- `assets/GOAL_TEMPLATE.md` — the `/goal` text to fill in and emit.
- `assets/LEDGER_TEMPLATE.md` — ledger shape, pin notation, the outcome-vs-causal note.
- `assets/seal_check.py` — executes every pin; fails on broken or unpinned.
- `assets/mutation_matrix.py` — disables one mechanism at a time; proves causal claims.

**Both runnable assets ship with a FLOOR (`_MIN_CLOSED_FINDINGS`, `_MIN_ATTACKS`/`_MIN_ALLOWED`),
and you must raise it as the campaign grows.** This is rule 22 applied to the tools themselves,
and it is not hypothetical: before the floors existed, an empty ledger made `seal_check` print
`0 broken · 0 with no runnable pin` and exit 0, and an unwired matrix printed `every claim
proven` and `rejected: none`. Those are precisely the strings `GOAL_TEMPLATE` asks the evaluator
to match on — so a tool that measured nothing would have ENDED THE CAMPAIGN, and the evaluator
could not have caught it, because it reads the transcript rather than running anything. The most
dangerous false green is the one in the instrument that decides you are finished.
