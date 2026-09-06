#!/usr/bin/env python3
"""
Role: Mutation test for a layered defence. Disables each mechanism in turn and re-runs the whole
      attack corpus, answering the question a passing suite cannot: is this mechanism
      load-bearing, or is it redundant cover — and does the mechanism the ledger CREDITS with a
      fix actually DO that job, or does something earlier reject the case first?
Key invariants (each one is a bug this file already had):
  - Disable at a FLAG THE CHECK ITSELF READS. Swapping a set for a "contains everything" stand-in
    is a silent no-op when the code uses set subtraction — and a matrix whose disable does
    nothing reports redundancy that does not exist, which is worse than no matrix.
  - VERIFY the mutation and the restore both landed. A missed restore leaves every later row
    running with the mechanism still off, so the "all on" column reports artefacts.
  - EVERY enforcement point needs a column. An unswitched check cannot be disabled, so it is
    reported as covered no matter how load-bearing it is. Assert the count.
  - A case that gets through with EVERYTHING ON proves nothing — it is an escape, not evidence.
  - Over-rejection is the other failure: a legitimate corpus must still be answered.

TEMPLATE — adapt the marked sections to the project.
"""

from __future__ import annotations

from typing import Any

# ── ADAPT ─────────────────────────────────────────────────────────────────────────────────────
# import the module under test, and the corpus as DATA
# from app.some.module import guarded_call
# from tests.security.corpus import ATTACKS, ALLOWED
guarded_module: Any = None  # the module whose _ENFORCE_* flags are flipped
ATTACKS: list[Any] = []  # each: .case_id, .sql / .payload
ALLOWED: list[Any] = []  # legitimate cases that must keep working

_LAYERS = ("<mechanism a>", "<mechanism b>")
_SWITCHES: dict[str, tuple[Any, str]] = {
    # "<mechanism a>": (guarded_module, "_ENFORCE_A"),
}

# The CAUSAL claim per mechanism: with THIS mechanism alone off, these cases must get THROUGH.
# Deliberately NOT stored on the corpus cases — the corpus records input and required outcome
# only, so a redesign with different checkpoints still has to satisfy it. This table describes
# THIS architecture and is expected to be rewritten when the architecture changes.
_CAUSAL_CLAIMS: dict[str, tuple[str, ...]] = {
    # "<mechanism a>": ("<case_id only this one holds>",),
}
# A mechanism may legitimately block nothing alone — but it must say so HERE, in writing.
# Silence is how an unmeasured check passes for a load-bearing one.
_NO_BLOCKING_CLAIM: dict[str, str] = {
    # "<mechanism b>": "measured non-load-bearing: <what actually holds these cases>",
}


def run_case(payload: Any) -> bool:
    """True if the payload was REFUSED. Adapt to the project's call + exception type."""
    raise NotImplementedError
# ──────────────────────────────────────────────────────────────────────────────────────────────


def _disable(layer: str) -> list[tuple[Any, str, Any]]:
    """Neutralise ONE mechanism; return (module, name, original) triples for restoration."""
    module, attribute = _SWITCHES[layer]
    saved = [(module, attribute, getattr(module, attribute))]
    setattr(module, attribute, False)
    if getattr(module, attribute) is not False:
        raise RuntimeError(f"disabling {layer!r} had no effect")
    return saved


def _restore(saved: list[tuple[Any, str, Any]]) -> None:
    """Put every swapped attribute back, and verify it landed."""
    for module, name, original in saved:
        setattr(module, name, original)
        if getattr(module, name) is not original:
            raise RuntimeError(f"failed to restore {name!r}")


# RAISE THESE to the real corpus size. They are the floor that stops a VACUOUS GREEN.
_MIN_ATTACKS = 1
_MIN_ALLOWED = 1


def _assert_matrix_is_wired() -> None:
    """A matrix that measures NOTHING must fail, not print OK.

    Reproduced, not theorised: with empty corpora or no layers, both loops iterate nothing,
    `run_case` is never called, and the run still prints `every claim proven` and
    `rejected: none` — which are exactly the strings the goal evaluator matches on. A false
    green here is undetectable by the evaluator BY CONSTRUCTION, in the one asset whose job is
    making the campaign terminate honestly.

    Raises:
        RuntimeError: the matrix would measure nothing, or a mechanism would go unexercised.
    """
    if len(ATTACKS) < _MIN_ATTACKS or len(ALLOWED) < _MIN_ALLOWED or not _LAYERS:
        raise RuntimeError(
            f"matrix not wired: {len(ATTACKS)} attacks (min {_MIN_ATTACKS}), "
            f"{len(ALLOWED)} allowed (min {_MIN_ALLOWED}), {len(_LAYERS)} layers"
        )
    # `_disable()` already hard-requires _LAYERS ⊆ _SWITCHES (KeyError otherwise). Equality adds
    # the SILENT direction: a switch registered but left out of _LAYERS is never disabled, never
    # tested, and the column audit below cannot see it either.
    if set(_LAYERS) != set(_SWITCHES):
        raise RuntimeError(f"_LAYERS != _SWITCHES: {sorted(set(_LAYERS) ^ set(_SWITCHES))}")
    # In BOTH tables, _NO_BLOCKING_CLAIM silently wins in the claims loop; in NEITHER, the layer
    # would only ever surface as UNPROVEN. Exactly one is the honest state.
    mismatched = [name for name in _LAYERS if (name in _CAUSAL_CLAIMS) == (name in _NO_BLOCKING_CLAIM)]
    if mismatched:
        raise RuntimeError(f"each layer needs exactly one causal/no-blocking claim: {mismatched}")


def _assert_every_enforcement_point_has_a_column() -> None:
    """Fail if the module grew a check this script cannot disable."""
    declared = {
        (module, name)
        for module, _ in _SWITCHES.values()
        for name in vars(module)
        if name.startswith(("_ENFORCE_", "_REQUIRE_"))
    }
    unmonitored = sorted(f"{m.__name__}.{n}" for m, n in declared - set(_SWITCHES.values()))
    if unmonitored:
        raise RuntimeError(f"enforcement switches with no matrix column: {unmonitored}")


def main() -> int:
    """Print the mechanism x attack matrix; non-zero if anything is actually broken."""
    _assert_matrix_is_wired()
    _assert_every_enforcement_point_has_a_column()
    escaped: list[str] = []
    through_when_off: dict[str, set[str]] = {layer: set() for layer in _LAYERS}

    for case in ATTACKS:
        blocked_all_on = run_case(case)
        if not blocked_all_on:
            escaped.append(case.case_id)
        for layer in _LAYERS:
            saved = _disable(layer)
            try:
                if not run_case(case):
                    through_when_off[layer].add(case.case_id)
            finally:
                _restore(saved)

    unproven: list[str] = []
    print("\nCausal claims (with this mechanism OFF, at least one case must get through):")
    for layer in _LAYERS:
        if layer in _NO_BLOCKING_CLAIM:
            print(f"  {layer:24s} - no blocking claim: {_NO_BLOCKING_CLAIM[layer][:60]}")
            continue
        named = set(_CAUSAL_CLAIMS.get(layer, ()))
        # An ESCAPE proves nothing: a case that always gets through would otherwise be counted as
        # evidence for whatever mechanism it was assigned to.
        proving = sorted((named & through_when_off[layer]) - set(escaped))
        shadowed = sorted(named - through_when_off[layer])
        status = f"proven by {proving[0]}" if proving else "UNPROVEN - nothing gets through"
        if shadowed:
            status += f" (shadowed: {', '.join(shadowed)})"
        print(f"  {layer:24s} - {status}")
        if not proving:
            unproven.append(layer)

    rejected = [case.case_id for case in ALLOWED if run_case(case)]
    print(f"\nOver-rejection check: rejected: {rejected or 'none'}")

    if escaped or rejected or unproven:
        print(
            f"\nFAIL - escaped: {escaped or 'none'} · legitimate refused: {rejected or 'none'} · "
            f"unproven claims: {unproven or 'none'}"
        )
        return 1
    print("\nOK - every attack blocked, every claim proven, every legitimate case answered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
