"""
Role: Seals the exact verdict-line grammar of contract §3.8 — the three forms (tuning /
      checkpoint / validation), the separators, the frozen provisional order, the per-split
      bracket, and every deviation `parse_verdict_line` must refuse.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.verdict and .exceptions (imported inside each test).
Key invariants:
  - Expected lines are written out by hand from the grammar, never produced by the formatter.
  - Every rejection has a positive control: the same line with the deviation undone parses.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

LOCK = hashlib.sha256(b"oracle lock").hexdigest()
RUNNER = hashlib.sha256(b"oracle runner").hexdigest()
RUN_ID = "20260906t120000z_0a1b2c3d"
MIDDLE_DOT = chr(0xB7)
PROVISIONAL_FOUR = ("FID", "THR", "IDENT", "ATTR")
TAIL = f"directional=- | run_id={RUN_ID} | lock=sha256:{LOCK} | runner=sha256:{RUNNER}"
TUNING_LINE = f"STEP1 TUNING: 1/17 PASS | provisional=4:FID,THR,IDENT,ATTR | {TAIL}"
BRACKET = f"(QS 3 {MIDDLE_DOT} NF 1 {MIDDLE_DOT} LANG 0 {MIDDLE_DOT} RET 2)"
CHECKPOINT_LINE = (
    f"STEP1 ACCEPTANCE: 2/17 PASS | provisional=4:FID,THR,IDENT,ATTR | "
    f"hidden 3/20 {BRACKET} | {TAIL}"
)
VALIDATION_LINE = f"STEP1 ACCEPTANCE: 17/17 PASS | provisional=0:- | validation=complete | {TAIL}"
TUNING_ZERO_LINE = TUNING_LINE.replace("provisional=4:FID,THR,IDENT,ATTR", "provisional=0:-")


def _tuning_fields(verdict: object) -> object:
    return verdict.VerdictFields(  # type: ignore[attr-defined]
        run_kind="tuning",
        passed=1,
        provisional=PROVISIONAL_FOUR,
        directional=(),
        run_id=RUN_ID,
        lock_sha256=LOCK,
        runner_sha256=RUNNER,
    )


def _checkpoint_fields(verdict: object) -> object:
    counters = verdict.HiddenCounters(  # type: ignore[attr-defined]
        total=3,
        limit=20,
        by_split={"QS": 3, "NF": 1, "LANG": 0, "RET": 2},
        invocations_under_lock=3,
    )
    return verdict.VerdictFields(  # type: ignore[attr-defined]
        run_kind="checkpoint",
        passed=2,
        provisional=PROVISIONAL_FOUR,
        directional=(),
        run_id=RUN_ID,
        lock_sha256=LOCK,
        runner_sha256=RUNNER,
        hidden=counters,
    )


def _validation_fields(verdict: object) -> object:
    return verdict.VerdictFields(  # type: ignore[attr-defined]
        run_kind="validation",
        passed=17,
        provisional=(),
        directional=(),
        run_id=RUN_ID,
        lock_sha256=LOCK,
        runner_sha256=RUNNER,
        validation_complete=True,
    )


def test_format_tuning_line_matches_grammar_exactly(instrument: InstrumentLoader) -> None:
    verdict = instrument("verdict")

    line = verdict.format_verdict_line(_tuning_fields(verdict))

    assert line == TUNING_LINE


def test_format_checkpoint_line_places_hidden_bracket_between_provisional_and_directional(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    line = verdict.format_verdict_line(_checkpoint_fields(verdict))

    assert line == CHECKPOINT_LINE


def test_format_validation_line_prints_complete_before_directional_and_no_hidden(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    line = verdict.format_verdict_line(_validation_fields(verdict))

    assert line == VALIDATION_LINE


def test_format_directional_gates_render_comma_separated_without_spaces(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")
    fields = replace(_tuning_fields(verdict), directional=("FID", "THR"))

    line = verdict.format_verdict_line(fields)

    assert " | directional=FID,THR | " in line
    assert line == TUNING_LINE.replace("directional=-", "directional=FID,THR")


def test_format_checkpoint_without_hidden_counters_is_refused(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")
    exceptions = instrument("exceptions")
    fields = replace(_checkpoint_fields(verdict), hidden=None)

    with pytest.raises(exceptions.VerdictFormatError):
        verdict.format_verdict_line(fields)
    # positive control: with the counters present the same fields format
    assert verdict.format_verdict_line(_checkpoint_fields(verdict)) == CHECKPOINT_LINE


@pytest.mark.parametrize("line", [TUNING_LINE, CHECKPOINT_LINE, VALIDATION_LINE])
def test_parse_round_trips_every_form(instrument: InstrumentLoader, line: str) -> None:
    verdict = instrument("verdict")

    fields = verdict.parse_verdict_line(line)

    assert verdict.format_verdict_line(fields) == line


def test_parse_checkpoint_recovers_counters_limit_and_split_order(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    fields = verdict.parse_verdict_line(CHECKPOINT_LINE)

    assert fields.run_kind == "checkpoint"
    assert fields.hidden.total == 3 and fields.hidden.limit == 20
    assert list(fields.hidden.by_split.items()) == [("QS", 3), ("NF", 1), ("LANG", 0), ("RET", 2)]
    assert fields.passed == 2 and fields.provisional == PROVISIONAL_FOUR


def test_parse_validation_sets_validation_complete_and_empty_provisional(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    fields = verdict.parse_verdict_line(VALIDATION_LINE)

    assert fields.run_kind == "validation"
    assert fields.validation_complete is True
    assert fields.provisional == () and fields.hidden is None


def test_parse_tuning_line_has_no_hidden_and_reports_lowercase_hashes(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    fields = verdict.parse_verdict_line(TUNING_LINE)

    assert fields.hidden is None and fields.validation_complete is False
    assert fields.lock_sha256 == LOCK and fields.runner_sha256 == RUNNER
    assert fields.run_id == RUN_ID


REJECTED_LINES = {
    "pipe_without_spaces": TUNING_LINE.replace(" | ", "|"),
    "bracket_uses_asterisk": CHECKPOINT_LINE.replace(f" {MIDDLE_DOT} ", " * "),
    "bracket_split_order": CHECKPOINT_LINE.replace(
        f"QS 3 {MIDDLE_DOT} NF 1", f"NF 1 {MIDDLE_DOT} QS 3"
    ),
    "bracket_missing_split": CHECKPOINT_LINE.replace(f" {MIDDLE_DOT} RET 2", ""),
    "uppercase_hex": TUNING_LINE.replace(f"lock=sha256:{LOCK}", f"lock=sha256:{LOCK.upper()}"),
    "sixty_three_hex": TUNING_LINE.replace(
        f"runner=sha256:{RUNNER}", f"runner=sha256:{RUNNER[:63]}"
    ),
    "lock_without_prefix": TUNING_LINE.replace(f"lock=sha256:{LOCK}", f"lock={LOCK}"),
    "provisional_with_space": TUNING_LINE.replace("FID,THR", "FID, THR"),
    "provisional_wrong_order": TUNING_LINE.replace("FID,THR,IDENT,ATTR", "THR,FID,IDENT,ATTR"),
    "provisional_count_mismatch": TUNING_LINE.replace("provisional=4:", "provisional=3:"),
    "provisional_annotated": TUNING_LINE.replace("ATTR", "ATTR(pending)"),
    "tuning_with_hidden_field": TUNING_LINE.replace(
        " | directional=-", f" | hidden 3/20 {BRACKET} | directional=-"
    ),
    "acceptance_without_hidden_or_validation": CHECKPOINT_LINE.replace(
        f" | hidden 3/20 {BRACKET}", ""
    ),
    "passed_above_seventeen": TUNING_LINE.replace("1/17 PASS", "18/17 PASS"),
    "hidden_total_not_max": CHECKPOINT_LINE.replace("hidden 3/20", "hidden 2/20"),
    "denominator_not_seventeen": TUNING_LINE.replace("1/17 PASS", "1/16 PASS"),
    "validation_after_directional": VALIDATION_LINE.replace(
        "validation=complete | directional=-", "directional=- | validation=complete"
    ),
    "trailing_garbage": TUNING_LINE + " | extra=1",
    "missing_step1_prefix": TUNING_LINE.replace("STEP1 ", ""),
    "unknown_kind": TUNING_LINE.replace("TUNING", "PRACTICE"),
    "directional_with_space": TUNING_LINE.replace("directional=-", "directional=FID, THR"),
    "run_id_not_in_the_determined_form": TUNING_LINE.replace(
        f"run_id={RUN_ID}", "run_id=oracle_run_0001"
    ),
    "run_id_uppercase_stamp": TUNING_LINE.replace(f"run_id={RUN_ID}", f"run_id={RUN_ID.upper()}"),
    "run_id_seven_hex": TUNING_LINE.replace(f"run_id={RUN_ID}", f"run_id={RUN_ID[:-1]}"),
}


@pytest.mark.parametrize("deviation", sorted(REJECTED_LINES))
def test_parse_rejects_every_deviation_from_the_grammar(
    instrument: InstrumentLoader, deviation: str
) -> None:
    verdict = instrument("verdict")
    exceptions = instrument("exceptions")
    line = REJECTED_LINES[deviation]
    assert line not in (TUNING_LINE, CHECKPOINT_LINE, VALIDATION_LINE)

    with pytest.raises(exceptions.VerdictFormatError):
        verdict.parse_verdict_line(line)
    # positive control: the undeviated forms parse
    verdict.parse_verdict_line(TUNING_LINE)
    verdict.parse_verdict_line(CHECKPOINT_LINE)


def test_verdict_format_error_is_a_mem01_error(instrument: InstrumentLoader) -> None:
    exceptions = instrument("exceptions")

    assert issubclass(exceptions.VerdictFormatError, exceptions.Mem01Error)


def test_verdict_fields_and_hidden_counters_are_frozen(instrument: InstrumentLoader) -> None:
    verdict = instrument("verdict")
    fields = _checkpoint_fields(verdict)

    with pytest.raises(AttributeError):
        fields.passed = 5  # type: ignore[misc]
    with pytest.raises(AttributeError):
        fields.hidden.total = 9  # type: ignore[misc]


def test_parse_accepts_provisional_zero_on_a_tuning_line_and_round_trips(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")

    fields = verdict.parse_verdict_line(TUNING_ZERO_LINE)

    assert fields.run_kind == "tuning" and fields.provisional == () and fields.hidden is None
    assert verdict.format_verdict_line(fields) == TUNING_ZERO_LINE


def test_format_refuses_hidden_on_tuning_stray_validation_complete_and_a_total_off_the_max(
    instrument: InstrumentLoader,
) -> None:
    verdict = instrument("verdict")
    exceptions = instrument("exceptions")
    checkpoint = _checkpoint_fields(verdict)

    with pytest.raises(exceptions.VerdictFormatError):
        verdict.format_verdict_line(replace(_tuning_fields(verdict), hidden=checkpoint.hidden))
    with pytest.raises(exceptions.VerdictFormatError):
        verdict.format_verdict_line(replace(_tuning_fields(verdict), validation_complete=True))
    with pytest.raises(exceptions.VerdictFormatError):
        verdict.format_verdict_line(replace(checkpoint, validation_complete=True))
    with pytest.raises(exceptions.VerdictFormatError):
        verdict.format_verdict_line(replace(checkpoint, hidden=replace(checkpoint.hidden, total=2)))
    assert verdict.format_verdict_line(checkpoint) == CHECKPOINT_LINE  # positive control
