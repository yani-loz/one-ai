"""
Role: Seals fix-registry row A28 / contract §16.17(a)-(b) on the RED gate — (a) the `logging`
      collector hears an `app.*` record emitted UNDER the runner's capture (where `app` does not
      propagate), formats it the way `app.log` does (message plus exception text and traceback
      when `exc_info` is set), counts a record once, and restores the `app` and root handlers;
      (b) hard-negative controls travel only the four text-carrying surfaces while positives
      and the Stage-A roster carry `logging`; and a Stage-A surface that produced NO output for
      any positive canary is listed in `diagnostics.surfaces_unexercised`, dropped from
      `surfaces_scored`, and makes `red.no_under_redaction` `incomplete` — never a vacuous
      PASS. The degraded-parse carrier itself (the REAL parser on the whole case text, its own
      record in the logging output) is sealed by `test_review_round_5_red_b.py`.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.gates.gate_red, .gates.context, .runner_logging, .lock,
      .criteria, .fixtures.red_cases (imported inside each test through the `instrument`
      loader); pytest capfd and monkeypatch; the criteria annex through `criteria_path`.
Key invariants:
  - Markers are unique nonce strings, never personal data; no canary text is ever asserted on
    a stream. The vacuity guard is driven with a stub `_surface_outputs` that returns every
    surface CLEAN (the canary removed) and an EMPTY `logging` output, which is exactly the
    shape a vacuous PASS hides behind; RED opens no database, so the `GateContext` carries no
    corpus and no probe.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from tests.tools.mem01_verify.conftest import InstrumentLoader

MARKER_APP = "oracle-round5-app-record-3c9e"
MARKER_EXCEPTION = "oracle-round5-raised-value-7b12"
MARKER_ROOT = "oracle-round5-root-record-a41d"
MARKER_APP_OUTSIDE = "oracle-round5-app-outside-58f0"
LOGGING_SURFACE = "logging"
TEXT_SURFACES = frozenset(
    {"email_body", "attachment_text", "attachment_structured_payload", "extraction_detail"}
)
HEX = "4f" * 32
ORG_ID = UUID("00000000-0000-4000-8000-0000000000aa")
LOGGING_OUTPUT_CLEAN = "parse_email: degraded parse on pathological nesting (key=sha256:0000)"


def _handlers() -> tuple[list[logging.Handler], list[logging.Handler], int]:
    root, app = logging.getLogger(), logging.getLogger("app")
    return list(root.handlers), list(app.handlers), root.level


def _count(lines: list[str], marker: str) -> int:
    return sum(marker in line for line in lines)


# ── (a) the collector under the runner's capture ─────────────────────────────────────────


def test_red_collector_hears_app_records_under_the_runner_capture_formatted_like_app_log(
    instrument: InstrumentLoader, capfd: pytest.CaptureFixture[str]
) -> None:
    gate_red = instrument("gates.gate_red")
    runner_logging = instrument("runner_logging")
    logger = logging.getLogger("app.round5")
    capfd.readouterr()

    with runner_logging.discard_app_logging(), gate_red._captured_logs() as lines:
        logger.warning("%s", MARKER_APP)
        try:
            raise ValueError(MARKER_EXCEPTION)
        except ValueError:
            logger.warning("degraded handoff", exc_info=True)
        collected = list(lines)
    captured = capfd.readouterr()

    joined = "\n".join(collected)
    assert MARKER_APP in joined, "the app record never reached the RED collector"
    assert MARKER_EXCEPTION in joined and "ValueError" in joined and "Traceback" in joined
    assert _count(collected, MARKER_APP) == 1 and _count(collected, MARKER_EXCEPTION) == 1
    assert MARKER_APP not in captured.out + captured.err
    assert MARKER_EXCEPTION not in captured.out + captured.err


def test_red_collector_restores_the_app_and_root_handlers_after_a_case(
    instrument: InstrumentLoader,
) -> None:
    gate_red = instrument("gates.gate_red")
    runner_logging = instrument("runner_logging")

    with runner_logging.discard_app_logging():
        before = _handlers()
        with gate_red._captured_logs():
            logging.getLogger("app.round5").warning("%s", MARKER_APP)
        after = _handlers()

    assert after == before


def test_red_collector_hears_root_records_outside_the_capture_and_counts_an_app_record_once(
    instrument: InstrumentLoader, capfd: pytest.CaptureFixture[str]
) -> None:
    gate_red = instrument("gates.gate_red")
    capfd.readouterr()

    with gate_red._captured_logs() as lines:
        logging.getLogger("oracle.round5").warning("%s", MARKER_ROOT)
        logging.getLogger("app.round5").warning("%s", MARKER_APP_OUTSIDE)
    captured = capfd.readouterr()

    assert _count(lines, MARKER_ROOT) == 1  # positive control: today's root listener still works
    assert _count(lines, MARKER_APP_OUTSIDE) == 1  # propagating to root AND app counts once
    assert MARKER_ROOT not in captured.out + captured.err


# ── (b) the surface matrix and the vacuity guard ─────────────────────────────────────────


def test_hard_negative_controls_travel_only_the_four_text_carrying_surfaces(
    instrument: InstrumentLoader,
) -> None:
    red_cases = instrument("fixtures.red_cases")
    assert red_cases.RED_NEGATIVES

    for control in red_cases.RED_NEGATIVES:
        assert set(control.surfaces) == TEXT_SURFACES, control.case_id
        assert LOGGING_SURFACE not in control.surfaces, control.case_id


def test_positive_canaries_and_the_stage_a_roster_still_carry_the_logging_surface(
    instrument: InstrumentLoader,
) -> None:
    red_cases = instrument("fixtures.red_cases")

    assert LOGGING_SURFACE in red_cases.STAGE_A_SURFACE_NAMES
    assert all(LOGGING_SURFACE in canary.surfaces for canary in red_cases.RED_POSITIVES)


def _gate_context(instrument: InstrumentLoader, criteria_path: Path, report_dir: Path) -> object:
    """A DB-free context for RED: no corpus, no probe, a temporary report directory."""
    release = instrument("lock").ReleaseInfo(
        path=report_dir.parent,
        name="step1-gold-v1",
        state="draft",
        lock_sha256=HEX,
        manifest={},
        criteria_path=criteria_path,
        visible_files_verified=0,
        hidden_files_verified=0,
    )
    return instrument("gates.context").GateContext(
        release=release,
        criteria=instrument("criteria").load_criteria(criteria_path),
        run_kind="tuning",
        split_evaluated="optimization",
        org_id=ORG_ID,
        corpus=None,
        corpus_snapshot=None,
        probe=None,
        fixtures_digest=HEX,
        report_dir=report_dir,
        hidden_root=None,
        versions={},
    )


def _clean_surfaces(
    instrument: InstrumentLoader, *, logging_output: str
) -> Callable[[str, str, str], dict[str, str]]:
    """A `_surface_outputs` stand-in: every text surface clean, `logging` as given."""
    positives = {canary.case_id for canary in instrument("fixtures.red_cases").RED_POSITIVES}

    def surface_outputs(case_id: str, text: str, marker: str) -> dict[str, str]:
        positive = case_id in positives
        outputs = dict.fromkeys(TEXT_SURFACES, text.replace(marker, "") if positive else text)
        outputs[LOGGING_SURFACE] = logging_output if positive else ""
        return outputs

    return surface_outputs


def _entry(result: object, criterion_id: str) -> dict:
    (entry,) = [e for e in result.criteria if e["id"] == criterion_id]  # type: ignore[attr-defined]
    return dict(entry)


async def test_an_unexercised_logging_surface_is_listed_and_makes_no_under_redaction_incomplete(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_red = instrument("gates.gate_red")
    monkeypatch.setattr(
        gate_red, "_surface_outputs", _clean_surfaces(instrument, logging_output="")
    )
    ctx = _gate_context(instrument, criteria_path, tmp_path / "report")

    result = await gate_red.evaluate(ctx)

    under = _entry(result, gate_red.UNDER_CRITERION_ID)
    assert under["status"] == "incomplete", under["status"]
    assert LOGGING_SURFACE in str(under["reason"])
    assert result.diagnostics["surfaces_unexercised"] == [LOGGING_SURFACE]
    assert LOGGING_SURFACE not in result.diagnostics["surfaces_scored"]


async def test_an_exercised_logging_surface_is_scored_and_nothing_is_unexercised(
    instrument: InstrumentLoader,
    criteria_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_red = instrument("gates.gate_red")
    monkeypatch.setattr(
        gate_red,
        "_surface_outputs",
        _clean_surfaces(instrument, logging_output=LOGGING_OUTPUT_CLEAN),
    )
    ctx = _gate_context(instrument, criteria_path, tmp_path / "report")

    result = await gate_red.evaluate(ctx)

    assert result.diagnostics["surfaces_unexercised"] == []
    assert LOGGING_SURFACE in result.diagnostics["surfaces_scored"]
    assert _entry(result, gate_red.UNDER_CRITERION_ID)["status"] == "PASS"  # positive control
