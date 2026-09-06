# /goal template for a hardening loop

Fill every `<...>`, delete the guidance lines, keep it under 4000 characters, and emit it in a
copyable block for the operator to paste.

## The one thing that decides whether this works

The evaluator **does not run commands and does not read files.** It reads the conversation. So
every clause must be something the working model's own output can DEMONSTRATE. Name the command,
and name the string in its output that counts as proof.

`"no critical issues remain"` cannot be evaluated. `"the transcript shows <cmd> printing
'0 broken'"` can.

## Template

```
/goal <TARGET> is hardened to the point where adversarial review finds only cosmetic issues.

Definition of done, all of which must be demonstrated IN THIS CONVERSATION by output you have
printed, from runs made AFTER your last edit:

1. VERIFICATION IS GREEN. The transcript shows, each from a single run after the final edit:
   - `<TEST CMD>` printing a passing summary with a non-zero test count
   - `<LINT CMD>` printing `<CLEAN STRING>`
   - `<SEAL CMD>` printing `0 broken` and `0 with no runnable pin`
   - `<MATRIX CMD>` printing `every claim proven` and `rejected: none`
   (Omit any line whose artifact does not exist yet; building it is part of the work.)

2. EVERY FIX IS SEALED CAUSALLY. For each finding closed this campaign, the transcript shows the
   pin FAILING with the fix reverted and PASSING with it restored. A fix without that
   demonstration is not closed.

3. ADVERSARIAL PASSES ARE EXHAUSTED. At least <N> independent adversarial agents (model: opus)
   have run against <TARGET>, the most recent reporting NO critical and NO medium findings — and
   you have personally verified each claim as CONFIRMED / QUALIFIED / REFUTED rather than
   accepting it. Each agent's report includes what it could NOT break and why.

4. NOTHING IS SILENTLY DEFERRED. Any issue not fixed is recorded in <LEDGER PATH> as an OPEN row
   stating WHY it is not fixed. Only two reasons are admissible: (a) it is cosmetic, or (b) the
   fix is a product/capability decision whose cost is priced in the row. "Needs its own round" is
   NOT admissible — do that round.

Constraints that must hold throughout:
- Never run two jobs against the database at once, and never edit while a verification runs.
- Adversarial agents must not run the test suite, touch the database, or edit files.
- Do not commit or push anything.
- <ANY PROJECT-SPECIFIC CONSTRAINT: ports that must not be touched, files that must not change>

Report progress against this list every turn, naming which items are demonstrated and which are
not. Stop after <M> turns if the condition is still unmet, and say plainly what is outstanding.
```

## Notes on filling it in

- **`<N>` adversarial agents**: 3–5 is a real campaign. Fewer and the "no findings" claim is weak.
- **`<M>` turn bound**: include one. The docs support it and it keeps a stuck loop from running
  indefinitely. 40–60 for a substantial campaign.
- **Clause 4 is the load-bearing one.** Without it, the loop terminates by relabelling unfixed
  work as "deferred". It is exactly the failure this clause exists to prevent, and it has
  happened.
- **Clause 2 is what makes it converge.** Without it the campaign accumulates green tests that
  prove nothing about which layer holds.
- Keep the `<CLEAN STRING>` literals exact — the evaluator matches on what it sees, so
  `All checks passed!` beats "lint is clean".
