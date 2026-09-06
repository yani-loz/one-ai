# <TARGET> — security & correctness findings ledger

**What this is.** Every confirmed finding, what closed it, and the test that keeps it closed. A
finding is CLOSED only when a named test would FAIL if the fix were reverted — not when the code
"looks fixed".

**Why it exists.** Without a ledger each review round re-litigates the same ground: reviewers
re-report what is already fixed, and the operator re-verifies from memory. This file is the
hand-off — a new round is handed the CLOSED list and told to go past it, and anything it
re-reports is answered by running the seal rather than by arguing.

**Is it still sealed? Run it and see:**

```
<SEAL COMMAND>
```

That EXECUTES every pin in this file and prints one line per finding, SEALED or BROKEN, naming
the pin that gave way. It exits non-zero if any seal is broken **or if any CLOSED row has no
runnable pin**, and CI runs it on every push. The table below is the map; that command is the
proof.

**How to use it.**
- Adding a finding: append a row with status OPEN and no pin.
- Closing one: write the fix AND the pin, then move it to CLOSED. A row with no pin is not closed,
  however convinced anyone is.
- Briefing the next review round: hand over this file.

Pin notation: `corpus:<case_id>` = an entry in the attack corpus that must be REFUSED ·
`<path/to/test_module.py>` = a test module that must pass with a non-zero count · otherwise name
the exact executable check.

**Outcome pins are not causal pins.** A `corpus:` pin proves the payload never reached the caller.
It does NOT prove that the mechanism named in "Closed by" is what stopped it — a case refused by
an EARLIER check would let its credited fix be deleted with the seal still green. The mutation
matrix carries the causal half: for each mechanism it names a case that MUST get through when that
one mechanism is disabled, and the build fails when a claim cannot be proven.

---

## CLOSED — <guarantee group, e.g. tenant isolation>

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|
| S1 | <what was actually wrong, in one line a stranger can act on> | <round/source> | <the mechanism that closes it> | `<executable pin>` |

## CLOSED — <second guarantee group>

| # | Finding | Round | Closed by | Pin |
|---|---|---|---|---|

---

## OPEN — accepted, deferred, or a founder decision

Only two admissible reasons to be here: it is **cosmetic**, or the fix is a **product decision
whose cost is priced below**. "Needs its own round" is not a reason — do the round.

| # | Item | Why it is open | Where |
|---|---|---|---|
| O1 | <the limit, stated so a future reviewer cannot "close" it with a fix that misses the shape> | <the cost of closing it, priced> | <ref> |
