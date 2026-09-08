"""
Role: Seals fix-registry row A41 / contract §16.17(b) (amended) on the RED gate's fragment scan —
      the `logging` output is NOT a positional transform of the case text, so the §16.9 fragment
      scan runs over the WHOLE output on that surface regardless of its length: a letter-bearing
      fragment of the canary planted at the end of a `logging` output longer than the whole-scan
      limit is caught (`survived_on:logging`, the case fails) for a canary placed in the first
      kilobyte AND for one placed beyond the 2,000,000-scalar cap. The region rule stays on the
      text-carrying surfaces (a fragment far outside the declared span of an over-limit
      `email_body` output is not a survivor), a fragment in a short log is caught, the whole
      secret at the end of an over-limit log is caught, and an over-limit log WITHOUT a fragment
      passes (the seal's red is attributable to the fragment alone).
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.gate_red and .fixtures.red_cases (imported inside each test
      through the `instrument` loader).
Key invariants:
  - Every surface map carries all five Stage-A surfaces, so `surface_missing` can never be the
    reason a case fails; the four text-carrying surfaces hold the case text with the canary
    REMOVED, so only the `logging` value (or the planted `email_body` tail) decides the verdict.
  - The fragment is the instrument's own first letter-bearing window (`gate_red._fragments`),
    asserted non-empty first, so the seal follows the §16.9 fragment rule rather than restating
    it; no canary text, fragment or output ever appears in an assertion message (R5).
  - Nothing here asserts HOW the instrument tells a positional surface from a non-positional one
    (the contract leaves that open) — only the observable verdicts.
"""

from __future__ import annotations

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

LOGGING = "logging"
TEXT_SURFACES = (
    "email_body",
    "attachment_text",
    "attachment_structured_payload",
    "extraction_detail",
)
SHORT_CLEAN_LOG = "parse_email: degraded parse on pathological nesting (key=sha256:0000)"
SHORT_LOG_LENGTH = 200
OVER_LIMIT_SLACK = 10_000
PICKS = ["first", "beyond_cap"]


def _pick(positives: tuple, pick: str) -> object:
    if pick == "first":
        return positives[0]
    return next(canary for canary in positives if canary.placement == "beyond_cap")


def _fragment(gate_red: object, canary: object) -> str:
    """The instrument's own first letter-bearing fragment of the canary (asserted to exist)."""
    fragments = gate_red._fragments(canary.canary_text)  # type: ignore[attr-defined]
    assert fragments, "the instrument yields no letter-bearing fragment for this canary"
    fragment = fragments[0]
    long_enough = len(fragment) >= gate_red.FRAGMENT_MIN_SCALARS  # type: ignore[attr-defined]
    letter_bearing = any(char.isalpha() for char in fragment)
    assert long_enough and letter_bearing, "not a §16.9 window"  # booleans: no text printed
    return fragment


def _over_limit_filler(gate_red: object) -> str:
    return "x" * (gate_red.REGION_SCAN_LIMIT + OVER_LIMIT_SLACK)  # type: ignore[attr-defined]


def _clean_surfaces(canary: object, *, logging_output: str) -> dict[str, str]:
    """All five surfaces: the text-carrying ones clean (canary removed), `logging` as given."""
    assert set(canary.surfaces) == {*TEXT_SURFACES, LOGGING}  # type: ignore[attr-defined]
    clean = canary.text_builder().replace(canary.canary_text, "")  # type: ignore[attr-defined]
    removed = canary.canary_text not in clean  # type: ignore[attr-defined]
    assert removed, "the canary was not removed from the clean text"  # boolean: no text printed
    outputs = dict.fromkeys(TEXT_SURFACES, clean)
    outputs[LOGGING] = logging_output
    return outputs


# ── the seal: a fragment beyond the whole-scan limit on the logging surface ──────────────


@pytest.mark.parametrize("pick", PICKS)
def test_a_fragment_at_the_end_of_an_over_limit_logging_output_is_caught(
    instrument: InstrumentLoader, pick: str
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, pick)
    fragment = _fragment(gate_red, canary)
    outputs = _clean_surfaces(canary, logging_output=_over_limit_filler(gate_red) + fragment)

    verdict = gate_red.score_canary(canary, outputs)

    assert verdict.passed is False, "a fragment past the whole-scan limit survived logging unseen"
    assert "survived_on:logging" in verdict.defects


# ── controls: green today, and they must stay green after the fix ────────────────────────


@pytest.mark.parametrize("pick", PICKS)
def test_an_over_limit_logging_output_without_a_fragment_passes(
    instrument: InstrumentLoader, pick: str
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, pick)
    outputs = _clean_surfaces(canary, logging_output=_over_limit_filler(gate_red))

    verdict = gate_red.score_canary(canary, outputs)

    assert verdict.passed is True, verdict.defects  # attribution: the filler alone is clean


def test_the_region_rule_still_applies_to_a_text_carrying_surface(
    instrument: InstrumentLoader,
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, "first")
    assert canary.canary_span[1] < gate_red.REGION_MARGIN  # the span sits near the start
    fragment = _fragment(gate_red, canary)
    outputs = _clean_surfaces(canary, logging_output=SHORT_CLEAN_LOG)
    outputs["email_body"] = outputs["email_body"] + _over_limit_filler(gate_red) + fragment

    verdict = gate_red.score_canary(canary, outputs)

    assert verdict.passed is True, verdict.defects  # far outside the span: not a survivor


@pytest.mark.parametrize("pick", PICKS)
def test_a_fragment_in_a_short_logging_output_is_caught(
    instrument: InstrumentLoader, pick: str
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, pick)
    fragment = _fragment(gate_red, canary)
    short_log = "x" * (SHORT_LOG_LENGTH - len(fragment)) + fragment
    outputs = _clean_surfaces(canary, logging_output=short_log)

    verdict = gate_red.score_canary(canary, outputs)

    assert verdict.passed is False
    assert "survived_on:logging" in verdict.defects


@pytest.mark.parametrize("pick", PICKS)
def test_the_whole_secret_at_the_end_of_an_over_limit_logging_output_is_caught(
    instrument: InstrumentLoader, pick: str
) -> None:
    gate_red = instrument("gates.gate_red")
    canary = _pick(instrument("fixtures.red_cases").RED_POSITIVES, pick)
    outputs = _clean_surfaces(
        canary, logging_output=_over_limit_filler(gate_red) + canary.canary_text
    )

    verdict = gate_red.score_canary(canary, outputs)

    assert verdict.passed is False
    assert "survived_on:logging" in verdict.defects
