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
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

# The answer-parsing primitives live in their own module: what an answer SAYS (parsing) and
# whether that is CORRECT (the typed rules below) are two responsibilities, and this file was
# over the 500-line ceiling holding both.
from scripts.ask_loop.answer_extraction import (
    _CITED_RE,
    _DATA_ASSERTION_RE,
    _INABILITY_RE,
    _LIST_INDEX_RE,
    _NO_DATA_PATTERNS,
    _UUID_RE,
    _extract_dates,
    _extract_numbers,
    _match_entity,
    _sentences,
)


def _grade_answerable(answer: str, gold: dict[str, Any]) -> tuple[str, str]:
    """Grade an answerable question by its answer_type; returns (verdict, detail)."""
    answer_type = gold.get("answer_type")
    expected = gold.get("expected") or {}

    if answer_type == "count":
        target, tolerance = int(expected["value"]), int(expected.get("tolerance", 0))
        # HEADLINE binding (verifier MUT11b finding): the count must come from the answer's
        # FIRST number-bearing sentence (the headline claim) — incidental per-item integers
        # later in the answer must never satisfy a count gold.
        # Strip markdown enumerators BEFORE splitting: they anchor to a LINE start, and the
        # sentence splitter breaks on "1." itself — so "Top senders:\n1. Acme sent 9" split
        # into a fragment ENDING in "1.", and 39 stored rows bound their count to that 1.
        listless = _LIST_INDEX_RE.sub(" ", answer)
        numbers: list[int] = []
        headline = ""
        for sentence in _sentences(listless):
            numbers = _extract_numbers(sentence[:300])
            if numbers:
                headline = sentence
                break
        if not numbers and target == 0:
            # A gold of ZERO is satisfied by saying so in words. Requiring a literal digit
            # failed every correct answer to a zero-count question — 24 of 24 stored rows on
            # one dev gold said "No PDF attachments were received" and were scored wrong.
            # The data-assertion guard keeps "I found 10" from qualifying.
            honest_zero = _NO_DATA_PATTERNS.search(answer) and not _DATA_ASSERTION_RE.search(answer)
            if honest_zero:
                return ("pass", "expected 0, answered in words")
        # THE FIRST number of that sentence is the claim; the rest are context. Accepting any
        # of them let "We have 25 active clients; the top 9 are listed below" satisfy a gold
        # of 9 — the model answered 25, and the qualifier rescued it.
        hit = bool(numbers) and abs(numbers[0] - target) <= tolerance
        if hit and _INABILITY_RE.search(headline):
            # A disclaimer in the CLAIM sentence means the number is a coincidence, not a
            # result ("I could not compute a total, but the search returned 2,900 matches").
            # Scoped to that sentence on purpose: searching the whole answer failed complete,
            # correct, fully-cited answers that merely ended with an honest scoping note —
            # "…but the remaining 10 addresses could not be matched to names" — which is
            # exactly the candour the prompt asks for. Only a would-be PASS is diverted.
            return ("pending_critic", "number appears inside a disclaimer — critic decides")
        return (
            "pass" if hit else "fail",
            f"expected {target}±{tolerance}, headline numbers {numbers[:8]}",
        )

    if answer_type == "date":
        target = date.fromisoformat(expected["value"])
        tolerance = int(expected.get("tolerance_days", 0))
        # HEADLINE binding, the same discipline counts got in MUT11b and dates never did.
        # Accepting ANY date in the answer passed a stored run whose headline states the wrong
        # date and then explicitly disowns, in writing, the incidental date that rescued it.
        dates: list[date] = []
        for sentence in _sentences(answer):
            dates = _extract_dates(sentence[:300])
            if dates:
                break
        hit = bool(dates) and abs((dates[0] - target).days) <= tolerance
        return ("pass" if hit else "fail", f"expected {target}±{tolerance}d, saw {dates[:6]}")

    if answer_type == "entity":
        hit = _match_entity(answer, expected)
        # Scoped to the sentence that NAMES the entity: "There is no information about Boyan
        # Bonev in the archive" names it while refusing, but a correct identification followed
        # by a caveat elsewhere is still a correct identification.
        naming = next((s for s in _sentences(answer) if _match_entity(s, expected)), "")
        if hit and (_INABILITY_RE.search(naming) or _NO_DATA_PATTERNS.search(naming)):
            return ("pending_critic", "entity named inside a disclaimer — critic decides")
        return ("pass" if hit else "fail", f"expected {expected.get('canonical')!r}")

    if answer_type == "list":
        items = expected.get("items") or []
        matched = sum(1 for item in items if _match_entity(answer, item))
        recall = matched / len(items) if items else 0.0
        needed = float(expected.get("min_recall", 1.0))
        # An enumeration spans many sentences and a trailing scoping note is candour, not a
        # refusal — so only a disclaimer in the FIRST sentence that names a gold item counts.
        first_item_sentence = next(
            (s for s in _sentences(answer) if any(_match_entity(s, i) for i in items)), ""
        )
        if recall >= needed and _INABILITY_RE.search(first_item_sentence):
            return ("pending_critic", "enumeration recalled inside a disclaimer")
        verdict = "pass" if recall >= needed else "fail"
        return (verdict, f"recall {matched}/{len(items)} (need {needed:.0%})")

    if answer_type == "text":
        return ("pending_critic", "free-text: claim-by-claim entailment required")

    return ("pending_critic", f"unknown answer_type {answer_type!r}")


def grade_one(record: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    """Grade one run record against its gold schema; returns the grade row."""
    gold = question.get("gold")
    if not gold or question.get("gold_status") != "authored":
        # Uniform row schema (N9): the summary tally reads intent_class on EVERY row —
        # an ungraded early-return without it KeyError'd the whole per-class breakdown.
        return {
            "qid": question["qid"],
            "intent_class": question.get("intent_class"),
            "split": question.get("split"),
            "verdict": "ungraded",
            "detail": "gold not authored",
        }

    asked = record.get("question")
    if asked is not None and asked != question.get("question"):
        # The run answered a DIFFERENT question than this gold now poses. qid is the only
        # join key, so an in-place edit to a question's text (retitling, translating,
        # tightening) silently re-points stored results at a gold they never saw — and
        # re-grading stored results against an edited question file is a supported workflow.
        return {
            "qid": question["qid"],
            "intent_class": question.get("intent_class"),
            "split": question.get("split"),
            "verdict": "stale",
            "detail": "question text changed since this result was produced — re-run it",
        }

    if record.get("error"):
        # An infrastructure failure is NOT a model answer. run_eval now survives a failed
        # question by emitting an error record with answer="" (N10); grading that as a model
        # `fail` would silently push transport/API flakiness into strict accuracy and blame
        # the question's intent class.
        return {
            "qid": question["qid"],
            "intent_class": question.get("intent_class"),
            "split": question.get("split"),
            "verdict": "error",
            "detail": str(record["error"])[:200],
        }

    answer = record["answer"]
    state = gold.get("state", "answerable")

    # Citation fidelity first (applies to every state): cited ids must exist in the tool
    # payloads the model actually saw — result_payload ONLY, never the whole call record:
    # serializing `arguments` too would let a model-invented id passed INTO a tool count as
    # evidence that came OUT of it, defeating the anti-fabrication gate (N5).
    transcript_ids = set()
    for call in record.get("tool_calls") or []:
        transcript_ids.update(
            u.lower() for u in _UUID_RE.findall(str(call.get("result_payload") or ""))
        )
    cited = [c.lower() for c in _CITED_RE.findall(answer)]
    invented = [c for c in cited if c not in transcript_ids]

    if state == "no_data":
        # Honesty = a no-data phrase AND no data claim anywhere in the answer. Citing an id or
        # naming a positive count of corpus objects is an assertion that data exists; such an
        # answer goes to the critic, never straight to pass on the strength of one negative
        # clause elsewhere in the sentence.
        honest = bool(_NO_DATA_PATTERNS.search(answer)) and not (
            cited or _DATA_ASSERTION_RE.search(answer)
        )
        if invented:
            verdict, detail = "fail", "cited evidence that tools never returned"
        elif honest:
            verdict, detail = "pass", "no-data honesty held"
        else:
            verdict, detail = ("pending_critic", "no explicit no-data phrase — critic decides")
    elif state == "ambiguous":
        candidates = gold.get("expected", {}).get("candidates") or []
        mentioned = sum(1 for c in candidates if _match_entity(answer, c))
        needed = int(gold.get("expected", {}).get("min_mentioned", 2))
        verdict = "pass" if mentioned >= needed else "fail"
        detail = f"candidates mentioned {mentioned}/{len(candidates)} (need {needed})"
    else:
        verdict, detail = _grade_answerable(answer, gold)

    # FABRICATED EVIDENCE FAILS IN EVERY STATE, including the ones the deterministic grader
    # cannot decide. Gating this on `verdict == "pass"` meant a pending_critic row (free-text
    # golds — the class where citations matter MOST) carried invented ids to the LLM critic
    # with the invented count computed and then discarded.
    min_citations = int((gold.get("evidence") or {}).get("min_citations", 0))
    if invented:
        verdict, detail = "fail", f"cited {len(invented)} id(s) no tool ever returned"
    elif verdict == "pass" and len(cited) < min_citations:
        verdict, detail = "fail", f"citation gate: cited={len(cited)} need={min_citations}"

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

    authored = json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    questions = {q["qid"]: q for q in authored}
    if len(questions) != len(authored):
        # A repeated qid means one authored gold was silently overwritten, so some answer is
        # graded against a gold it never saw — and the coverage check would still report
        # complete, because every qid in the file has a row.
        seen = Counter(q["qid"] for q in authored)
        repeated = sorted(qid for qid, n in seen.items() if n > 1)
        raise SystemExit(
            f"question file has duplicate qids {repeated} — each qid must identify one gold"
        )
    results_path = Path(args.results)
    grades, worksheet = [], []
    seen_qids: list[str] = []
    unknown_qids: list[str] = []
    with results_path.open(encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            question = questions.get(record["qid"])
            if question is None:
                unknown_qids.append(record["qid"])
                continue
            seen_qids.append(record["qid"])
            grade = grade_one(record, question)
            grades.append(grade)
            if grade["verdict"] == "pending_critic":
                # Tool payloads ride along (capped) so the critic can distinguish
                # transcript-SUPPORTED extra claims from genuinely unsupported ones —
                # the ≤2% gate counts only claims absent from BOTH gold AND transcript.
                payloads = " ".join(
                    str(c.get("result_payload") or "") for c in (record.get("tool_calls") or [])
                )[:8000]
                worksheet.append(
                    {
                        "qid": record["qid"],
                        "question": question["question"],
                        "answer": record["answer"],
                        "gold_state": (question.get("gold") or {}).get("state"),
                        "atomic_claims": (question.get("gold") or {}).get("atomic_claims"),
                        "tool_payloads_excerpt": payloads,
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
        # .get, not subscript: rows are uniform now, but the summary must never die on one
        # irregular row again (N9) — grades.jsonl from older runs may still lack the key.
        by_class.setdefault(g.get("intent_class") or "?", Counter())[g["verdict"]] += 1
    graded = tally["pass"] + tally["fail"]
    # COVERAGE IS PART OF THE RESULT. Iterating results.jsonl alone cannot tell a deliberate
    # 33-question subset from a 59-question run whose rows were lost: a truncated file simply
    # yields a smaller denominator, and deleting the failures turns 0.412 into a clean 1.000.
    # An accuracy figure is therefore printed only when the coverage it rests on is stated.
    missing = sorted(set(questions) - set(seen_qids))
    duplicates = sorted({q for q in seen_qids if seen_qids.count(q) > 1})
    complete = not missing and not duplicates and not unknown_qids
    summary = {
        "expected_questions": len(questions),
        "results_rows": len(seen_qids),
        "missing_qids": missing[:20],
        "duplicate_qids": duplicates[:20],
        "unknown_qids": unknown_qids[:20],
        "coverage_complete": complete,
        "graded": graded,
        "pass": tally["pass"],
        "fail": tally["fail"],
        "pending_critic": tally["pending_critic"],
        "ungraded": tally["ungraded"],
        # Neither infrastructure failures nor results whose question has since been edited
        # are folded into graded accuracy — both are reported so they cannot pass unnoticed.
        "errored": tally["error"],
        "stale": tally["stale"],
        "strict_accuracy_graded_only": round(tally["pass"] / graded, 3) if graded else None,
        "by_class": {c: dict(v) for c, v in sorted(by_class.items())},
    }
    print(json.dumps(summary, indent=1))
    if not complete:
        print(
            f"!! PARTIAL COVERAGE — {len(missing)} question(s) have no result row, "
            f"{len(duplicates)} duplicated, {len(unknown_qids)} row(s) match no question. "
            "The accuracy above describes only the rows present; it is NOT this run's score."
        )
    print(f"grades: {out_dir / 'grades.jsonl'}")
    if worksheet:
        print(f"critic worksheet ({len(worksheet)} items): {out_dir / 'critic_worksheet.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
