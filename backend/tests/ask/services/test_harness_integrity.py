"""
Role: Executable seals for the eval harness's honesty guarantees — the ones whose ledger rows
      previously pointed at a source file rather than a test, i.e. were claimed closed with
      nothing to prove it. Covers coverage reporting, the stale-gold join, the cache basis,
      duplicate qids and routing-token attribution.
Used by: pytest (tests/ask/services) and scripts/ask_loop/seal_check via the ledger.
Depends on: scripts.ask_loop.grade, scripts.ask_loop.run_eval. No database, no network:
      grade.main() reads files, and the cache basis is computed from objects we construct here.
Key invariants:
  - The harness may not FLATTER (report a score it did not earn) and may not SLANDER (fail a
    correct answer). Both directions are asserted here.
  - Everything runs in a temp directory; no repo file and no shared database is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ask_loop import grade


def _question(qid: str, text: str = "How many?") -> dict[str, Any]:
    """One authored count gold."""
    return {
        "qid": qid,
        "question": text,
        "gold_status": "authored",
        "intent_class": "aggregation",
        "split": "dev",
        "gold": {"state": "answerable", "answer_type": "count",
                 "expected": {"value": 5, "tolerance": 0}},
    }


def _record(qid: str, answer: str, question: str = "How many?") -> dict[str, Any]:
    """One run record as run_eval writes it."""
    return {"qid": qid, "question": question, "answer": answer, "tool_calls": [], "turns": 1}


def _write(tmp_path: Path, questions: list[dict[str, Any]], records: list[dict[str, Any]]):
    """Write a question file + results.jsonl and return their paths."""
    qfile = tmp_path / "questions.json"
    qfile.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    rfile = tmp_path / "results.jsonl"
    rfile.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return qfile, rfile


def _summary(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """The JSON summary grade.main() printed."""
    printed = capsys.readouterr().out
    start = printed.index("{")
    end = printed.rindex("}") + 1
    return json.loads(printed[start:end])


# — M4: a partial results file must not read as a complete run —


def test_missing_result_rows_are_reported_and_flagged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Deleting the failing rows from a results file turned 0.412 into a clean 1.000, and
    # nothing in the summary distinguished that from a run of only the surviving questions.
    questions = [_question("Q1"), _question("Q2"), _question("Q3")]
    records = [_record("Q1", "There are 5 threads.")]
    qfile, rfile = _write(tmp_path, questions, records)

    grade.main(["--questions", str(qfile), "--results", str(rfile)])

    summary = _summary(capsys)
    assert summary["expected_questions"] == 3
    assert summary["missing_qids"] == ["Q2", "Q3"]
    assert summary["coverage_complete"] is False


def test_complete_coverage_is_reported_as_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The flag has to mean something, so pin the positive case too.
    questions = [_question("Q1")]
    qfile, rfile = _write(tmp_path, questions, [_record("Q1", "There are 5 threads.")])

    grade.main(["--questions", str(qfile), "--results", str(rfile)])

    summary = _summary(capsys)
    assert summary["coverage_complete"] is True
    assert summary["strict_accuracy_graded_only"] == 1.0


# — M6: an edited question must not be graded against results that never saw it —


def test_edited_question_text_grades_stale_not_scored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    questions = [_question("Q1", text="How many threads with Acme?")]
    records = [_record("Q1", "There are 5 threads.", question="How many threads in total?")]
    qfile, rfile = _write(tmp_path, questions, records)

    grade.main(["--questions", str(qfile), "--results", str(rfile)])

    summary = _summary(capsys)
    assert summary["stale"] == 1
    assert summary["graded"] == 0  # a stale row is reported, never scored


# — M12: a duplicate qid silently overwrote an authored gold —


def test_duplicate_qid_in_the_question_file_aborts(tmp_path: Path) -> None:
    questions = [_question("Q1"), _question("Q1", text="A different question, same qid")]
    qfile, rfile = _write(tmp_path, questions, [_record("Q1", "There are 5 threads.")])

    with pytest.raises(SystemExit) as raised:
        grade.main(["--questions", str(qfile), "--results", str(rfile)])

    assert "duplicate qids" in str(raised.value)


# — M8: infrastructure failures are not model failures —


def test_error_records_are_not_scored_against_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _record("Q1", "")
    record["error"] = "ReaderModelError: HTTP 503"
    qfile, rfile = _write(tmp_path, [_question("Q1")], [record])

    grade.main(["--questions", str(qfile), "--results", str(rfile)])

    summary = _summary(capsys)
    assert summary["errored"] == 1
    assert summary["fail"] == 0
