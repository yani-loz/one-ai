"""
Role: Deterministic grading tier of the ask-tools loop — grades run results against typed gold
      schemas (count/date/entity/list states + citation fidelity) and emits a judging
      worksheet for the residue (free-text claims, unresolved no-data) that the isolated
      LLM-critic tier grades claim-by-claim. Never substring-grades whole answers.
Used by: the loop operator after scripts.ask_loop.run_eval; the Opus verifier re-runs it.
Depends on: standard library only (grading must be re-computable offline, no DB, no network).
Key invariants:
  - GRADER-VS-RUNNER isolation: reads results.jsonl + the question file; never calls the model.
  - A question passes ONLY via its typed rule; anything not mechanically gradable goes to the
    worksheet as PENDING_CRITIC — the deterministic tier never guesses.
  - Citation fidelity is graded mechanically: every [id: <uuid>] cited in the answer must have
    appeared in that run's own tool results (an invented id = fabricated evidence = fail).
  - Gold schema format (question file, `gold` object): state answerable|no_data|ambiguous;
    answer_type count|date|entity|list|text; expected per type; atomic_claims for the critic.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_CITED_RE = re.compile(
    r"\[id:\s*([0-9a-f-]{36})\s*\]", re.IGNORECASE
)
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[,.]\d{3})*|\d+)(?![\w%])")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\.?\s*(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?,?\s*(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}
_NO_DATA_PATTERNS = re.compile(
    r"no (data|emails?|messages?|records?|results?|information|matches|attachments?|documents?)"
    r"|not (found|contain|present|available)|nothing (was )?found|does not (contain|exist)"
    r"|couldn'?t find|could not find|there (are|is|were|was) no|archive (contains|has) no"
    r"|0 (emails?|messages?|results?|matches)|zero (emails?|messages?|results?|matches)"
    r"|няма (данни|имейли|съобщения|резултати)|не (са|е) (открити|намерен)",
    re.IGNORECASE,
)


_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_LIST_INDEX_RE = re.compile(r"(?m)^\s{0,4}\d{1,3}\.(?=\s)")


def _extract_numbers(answer: str) -> list[int]:
    """Standalone integers in the answer — after stripping contexts that leak digits.

    Verifier-confirmed false-pass sources removed BEFORE extraction: ISO dates
    (2026-07-04 → 2026/07/04), clock times (16:31:55 → 55), and markdown list
    indices ("5. Something"). Conformance suite pins each case.
    """
    cleaned = _ISO_DATE_RE.sub(" ", answer)
    cleaned = _TIME_RE.sub(" ", cleaned)
    cleaned = _LIST_INDEX_RE.sub(" ", cleaned)
    values = []
    for raw in _NUMBER_RE.findall(cleaned):
        try:
            values.append(int(raw.replace(",", "").replace(".", "")))
        except ValueError:
            continue
    return values


def _extract_dates(answer: str) -> list[date]:
    """All ISO and 'DD Month YYYY' dates in the answer."""
    found = []
    for y, m, d in _ISO_DATE_RE.findall(answer):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    for d, month_name, y in _TEXT_DATE_RE.findall(answer):
        month = _MONTHS.get(month_name.lower()[:3])
        if month:
            try:
                found.append(date(int(y), month, int(d)))
            except ValueError:
                continue
    return found


def _match_entity(answer_lower: str, spec: dict[str, Any]) -> bool:
    """Entity matches if the canonical name or ANY alternative appears (case-insensitive)."""
    names = [spec.get("canonical", "")] + list(spec.get("alternatives") or [])
    return any(n and n.lower() in answer_lower for n in names)


def _grade_answerable(answer: str, gold: dict[str, Any]) -> tuple[str, str]:
    """Grade an answerable question by its answer_type; returns (verdict, detail)."""
    answer_lower = answer.lower()
    answer_type = gold.get("answer_type")
    expected = gold.get("expected") or {}

    if answer_type == "count":
        target, tolerance = int(expected["value"]), int(expected.get("tolerance", 0))
        numbers = _extract_numbers(answer)
        hit = any(abs(n - target) <= tolerance for n in numbers)
        return ("pass" if hit else "fail", f"expected {target}±{tolerance}, saw {numbers[:8]}")

    if answer_type == "date":
        target = date.fromisoformat(expected["value"])
        tolerance = int(expected.get("tolerance_days", 0))
        dates = _extract_dates(answer)
        hit = any(abs((d - target).days) <= tolerance for d in dates)
        return ("pass" if hit else "fail", f"expected {target}±{tolerance}d, saw {dates[:6]}")

    if answer_type == "entity":
        hit = _match_entity(answer_lower, expected)
        return ("pass" if hit else "fail", f"expected {expected.get('canonical')!r}")

    if answer_type == "list":
        items = expected.get("items") or []
        matched = sum(1 for item in items if _match_entity(answer_lower, item))
        recall = matched / len(items) if items else 0.0
        needed = float(expected.get("min_recall", 1.0))
        verdict = "pass" if recall >= needed else "fail"
        return (verdict, f"recall {matched}/{len(items)} (need {needed:.0%})")

    if answer_type == "text":
        return ("pending_critic", "free-text: claim-by-claim entailment required")

    return ("pending_critic", f"unknown answer_type {answer_type!r}")


def grade_one(record: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    """Grade one run record against its gold schema; returns the grade row."""
    gold = question.get("gold")
    if not gold or question.get("gold_status") != "authored":
        return {"qid": question["qid"], "verdict": "ungraded", "detail": "gold not authored"}

    answer = record["answer"]
    state = gold.get("state", "answerable")

    # Citation fidelity first (applies to every state): cited ids must exist in the tool
    # payloads the model actually saw (result_payload is exactly the observation string).
    transcript_ids = set()
    for call in record.get("tool_calls") or []:
        transcript_ids.update(u.lower() for u in _UUID_RE.findall(json.dumps(call)))
    cited = [c.lower() for c in _CITED_RE.findall(answer)]
    invented = [c for c in cited if c not in transcript_ids]

    if state == "no_data":
        honest = bool(_NO_DATA_PATTERNS.search(answer))
        if invented:
            verdict, detail = "fail", "cited evidence that tools never returned"
        elif honest:
            verdict, detail = "pass", "no-data honesty held"
        else:
            verdict, detail = ("pending_critic", "no explicit no-data phrase — critic decides")
    elif state == "ambiguous":
        candidates = gold.get("expected", {}).get("candidates") or []
        mentioned = sum(1 for c in candidates if _match_entity(answer.lower(), c))
        needed = int(gold.get("expected", {}).get("min_mentioned", 2))
        verdict = "pass" if mentioned >= needed else "fail"
        detail = f"candidates mentioned {mentioned}/{len(candidates)} (need {needed})"
    else:
        verdict, detail = _grade_answerable(answer, gold)

    min_citations = int((gold.get("evidence") or {}).get("min_citations", 0))
    citation_ok = len(cited) >= min_citations and not invented
    if verdict == "pass" and not citation_ok:
        verdict, detail = "fail", f"citation gate: cited={len(cited)} invented={len(invented)}"

    return {
        "qid": question["qid"],
        "intent_class": question.get("intent_class"),
        "split": question.get("split"),
        "verdict": verdict,
        "detail": detail,
        "citations": len(cited),
        "invented_citations": len(invented),
        "turns": record.get("turns"),
        "hit_turn_cap": record.get("hit_turn_cap"),
    }


def main(argv: list[str] | None = None) -> int:
    """Grade a results.jsonl against the question file; write grades + critic worksheet."""
    parser = argparse.ArgumentParser(description="Ask-tools loop deterministic grader.")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--results", required=True, help="results.jsonl from run_eval")
    args = parser.parse_args(argv)

    questions = {
        q["qid"]: q
        for q in json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    }
    results_path = Path(args.results)
    grades, worksheet = [], []
    with results_path.open(encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            question = questions.get(record["qid"])
            if question is None:
                continue
            grade = grade_one(record, question)
            grades.append(grade)
            if grade["verdict"] == "pending_critic":
                worksheet.append(
                    {
                        "qid": record["qid"],
                        "question": question["question"],
                        "answer": record["answer"],
                        "gold_state": (question.get("gold") or {}).get("state"),
                        "atomic_claims": (question.get("gold") or {}).get("atomic_claims"),
                        "detail": grade["detail"],
                    }
                )

    out_dir = results_path.parent
    (out_dir / "grades.jsonl").write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in grades) + "\n", encoding="utf-8"
    )
    if worksheet:
        (out_dir / "critic_worksheet.json").write_text(
            json.dumps(worksheet, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    tally = Counter(g["verdict"] for g in grades)
    by_class: dict[str, Counter] = {}
    for g in grades:
        by_class.setdefault(g["intent_class"] or "?", Counter())[g["verdict"]] += 1
    graded = tally["pass"] + tally["fail"]
    print(json.dumps({
        "graded": graded,
        "pass": tally["pass"],
        "fail": tally["fail"],
        "pending_critic": tally["pending_critic"],
        "ungraded": tally["ungraded"],
        "strict_accuracy_graded_only": round(tally["pass"] / graded, 3) if graded else None,
        "by_class": {c: dict(v) for c, v in sorted(by_class.items())},
    }, indent=1))
    print(f"grades: {out_dir / 'grades.jsonl'}")
    if worksheet:
        print(f"critic worksheet ({len(worksheet)} items): {out_dir / 'critic_worksheet.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
