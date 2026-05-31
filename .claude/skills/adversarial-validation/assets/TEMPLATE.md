<!--
  Test-case template. Copy to testing/<NN>_<target>/TC-<TT>-<NNN>_<slug>.md and fill
  every section. Author the top half BEFORE running; write the "Execution result" block
  back into this same file AFTER running. See testing/README.md for legend + tags.
-->

# TC-<TT>-<NNN>: <concise title>

| Field | Value |
|---|---|
| **ID** | TC-<TT>-<NNN> |
| **Target** | <e.g. Infrastructure + AuthN/AuthZ> |
| **Suite** | <e.g. Token validation> |
| **Type** | Positive / Negative / Boundary / Adversarial / Concurrency / Fuzz |
| **Severity if it fails** | Critical / High / Medium / Low / Info |
| **Status** | Draft → Executed |
| **Result** | ⬜ Not run |
| **Finding tag** | NEW / CONFIRMS-FIXED / REFUTES-FIX / CONFIRMS-DOCUMENTED / — |

## Objective
What invariant/contract this verifies, in one or two sentences.

## Break hypothesis
The concrete prediction of *how and where* this fails — the attacker's bet. (Even for a
positive test, state what a violation would look like.)

## Preconditions
Environment, seed/setup, fixtures, the run-stamped namespace used.

## Steps
1. …
2. …

## Expected result
The exact status codes / body shape / side effects the contract requires.

## Harness
Script: `harness/<script>.py` · run: `docker compose exec -T backend python - < testing/<NN>_<target>/harness/<script>.py`

---

## Execution result
<!-- Filled AFTER running. Keep raw evidence — status codes + bodies — not prose summaries. -->

- **Run at:** <YYYY-MM-DD HH:MM local>
- **Result:** ✅ Pass / ❌ Fail / ⚠️ Pass-with-concern
- **Finding tag:** <NEW / CONFIRMS-FIXED / REFUTES-FIX / CONFIRMS-DOCUMENTED / —>

**Actual behavior**

> What the live system actually did.

**Evidence**

```
<raw request/response, status codes, harness stdout — the proof>
```

**Verdict**

<Did the defense hold or break? If it broke: severity, blast radius, and the precise code
path (`file:line`). If it confirms/refutes a prior audit claim, say which.>

**Notes / follow-up**

<Remediation pointer, related cases, or a tracked-item cross-reference.>
