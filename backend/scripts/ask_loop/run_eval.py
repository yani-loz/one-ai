"""
Role: Eval runner CLI — answers a set of gold questions through the app.ask agent on the
      person-scoped reader plane, with per-(config, question) answer caching and full
      per-question transcripts (the loop's telemetry atom).
Used by: the loop operator. Host-side from backend/:
  uv run python -m scripts.ask_loop.run_eval --questions <file> --person <uuid> \
      --label parent-rung1 [--qids Q001,Q002 | --split dev] [--model-params '{...}'] \
      [--parallel 4] [--no-cache]
Depends on: app.ask (agent runner, shared-core registry, Together adapter), app.core.database.
Key invariants:
  - The config hash pins model id + tool serialization + system prompt + model params +
    turn cap: any change = different hash = cache miss = honest re-measurement.
  - The runner reads ONLY question text (never gold answers) — grading is a separate pass.
  - Results + cache live under the artifacts dir OUTSIDE the repo (PII boundary);
    override with --artifacts or ASK_LOOP_ARTIFACTS.
  - Each question runs on its own reader_session(org, person) — permission probes are just
    runs with a different --person (no special casing, the plane itself enforces scope).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.services.agent_runner import AskAgentRunner, AskResult
from app.ask.tools.registry import ToolRegistry
from app.ask.tools.shared_core import build_shared_core_registry
from app.core.database import (
    engine,
    global_engine,
    reader_engine,
    reader_session,
    tenant_engine,
)

_DEV_INGEST_ORG = UUID("d1500000-0000-0000-0000-000000000001")
_DEFAULT_ARTIFACTS = Path(r"C:\Users\Yani_\Desktop\In-Progress\One AI\Benchmarks\_ask_loop")


def _artifacts_root(override: str | None) -> Path:
    """Resolve the artifacts root (CLI flag > env > default), creating it if needed."""
    root = Path(override or os.environ.get("ASK_LOOP_ARTIFACTS") or _DEFAULT_ARTIFACTS)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _config_hash(
    registry: ToolRegistry, runner: AskAgentRunner, model: str, model_params: dict[str, Any]
) -> str:
    """Hash everything that changes answers: model, tools, prompt, params, turn cap — AND the
    tool/runner SOURCE code, because executor semantics (SQL, response envelopes) change
    answers without changing the serialized tool specs (measured: MUT2 was silently served
    MUT1's cache)."""
    import app.ask.services.agent_runner as _runner_mod
    import app.ask.services.router as _router_mod
    import app.ask.services.sql_pipeline as _pipe_mod
    import app.ask.tools.shared_core as _tools_mod
    import app.ask.tools.sql_guard as _guard_mod
    import app.ask.tools.sql_tool as _sql_mod

    code = b""
    for module in (_tools_mod, _runner_mod, _router_mod, _guard_mod, _sql_mod, _pipe_mod):
        code += Path(module.__file__).read_bytes()
    basis = json.dumps(
        {
            "model": model,
            "tools": registry.llm_tools(),
            "system_prompt": runner._system_prompt,  # noqa: SLF001 — deliberate pin
            "model_params": model_params,
            "max_turns": runner._max_turns,  # noqa: SLF001
            "code_sha": hashlib.sha256(code).hexdigest(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _select_questions(
    payload: dict[str, Any], qids: set[str] | None, split: str | None
) -> list[dict[str, Any]]:
    """Filter the question file by explicit qids or by split tag."""
    questions = payload["questions"]
    if qids:
        selected = [q for q in questions if q["qid"] in qids]
        missing = qids - {q["qid"] for q in selected}
        if missing:
            raise SystemExit(f"Unknown qids: {sorted(missing)}")
        return selected
    if split:
        return [q for q in questions if q.get("split") == split]
    return list(questions)


async def _answer_one(
    runner: AskAgentRunner,
    org_id: UUID,
    person_id: UUID | None,
    question: dict[str, Any],
    cache_dir: Path,
    use_cache: bool,
    semaphore: asyncio.Semaphore,
    arm: str = "generalist",
    client: Any = None,
) -> dict[str, Any]:
    """Answer one question (cache-first); return the JSONL record.

    arm='routed': classify the question first, then run with the class-scoped registry
    and the class procedure appended to the system prompt (generalist on any fallback).
    """
    cache_file = cache_dir / f"{question['qid']}.json"
    if use_cache and await asyncio.to_thread(cache_file.exists):
        cached_text = await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
        record = json.loads(cached_text)
        record["cached"] = True
        return record

    routed_class: str | None = None
    async with semaphore:
        if arm == "xiyan-routed" and client is not None:
            from app.ask.services.router import classify_question
            from app.ask.services.sql_pipeline import SQL_PIPELINE_CLASSES, answer_via_sql

            routed_class = await classify_question(client, question["question"])
            if routed_class in SQL_PIPELINE_CLASSES:
                async with reader_session(org_id, person_id) as session:
                    piped = await answer_via_sql(client, session, question["question"])
                if piped is not None:
                    record = {
                        "qid": question["qid"],
                        "source_id": question.get("source_id"),
                        "intent_class": question.get("intent_class"),
                        "routed_class": routed_class,
                        "question": question["question"],
                        **piped,
                        "cached": False,
                    }
                    await asyncio.to_thread(
                        cache_file.write_text,
                        json.dumps(record, ensure_ascii=False, indent=1),
                        encoding="utf-8",
                    )
                    return record
            # fall through to the normal agent below
        if arm == "routed" and client is not None:
            from app.ask.services.router import (
                classify_question,
                prompt_addendum_for_class,
                registry_for_class,
            )

            routed_class = await classify_question(client, question["question"])
            runner = AskAgentRunner(
                client,
                registry_for_class(runner._registry, routed_class),  # noqa: SLF001
                today=date.fromisoformat("1970-01-01"),  # overridden by system_prompt below
                max_turns=runner._max_turns,  # noqa: SLF001
                model_params=runner._model_params,  # noqa: SLF001
                system_prompt=runner._system_prompt  # noqa: SLF001
                + prompt_addendum_for_class(routed_class),
            )
        async with reader_session(org_id, person_id) as session:
            result: AskResult = await runner.run(question["question"], session)

    record = {
        "qid": question["qid"],
        "source_id": question.get("source_id"),
        "intent_class": question.get("intent_class"),
        "routed_class": routed_class,
        "question": question["question"],
        "answer": result.answer,
        "turns": result.turns,
        "hit_turn_cap": result.hit_turn_cap,
        "tool_calls": [asdict(c) for c in result.tool_calls],
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_seconds": result.latency_seconds,
        "cached": False,
    }
    await asyncio.to_thread(
        cache_file.write_text, json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return record


async def _answer_all(
    runner: AskAgentRunner,
    org: UUID,
    person_id: UUID | None,
    questions: list[dict[str, Any]],
    cache_dir: Path,
    use_cache: bool,
    parallel: int,
    arm: str = "generalist",
    client: Any = None,
) -> list[dict[str, Any]]:
    """Answer every selected question concurrently (bounded), disposing engines after."""
    semaphore = asyncio.Semaphore(parallel)
    try:
        return list(
            await asyncio.gather(
                *(
                    _answer_one(
                        runner, org, person_id, q, cache_dir, use_cache, semaphore,
                        arm=arm, client=client,
                    )
                    for q in questions
                )
            )
        )
    finally:
        for eng in (engine, tenant_engine, global_engine, reader_engine):
            await eng.dispose()


def main(argv: list[str] | None = None) -> int:
    """CLI entry: load + select questions, answer them, write results + reproducibility meta."""
    parser = argparse.ArgumentParser(description="Ask-tools loop eval runner.")
    parser.add_argument("--questions", required=True, help="question file (JSON)")
    parser.add_argument("--label", required=True, help="run label (e.g. parent-rung1)")
    parser.add_argument("--qids", default=None, help="comma-separated qid subset")
    parser.add_argument("--split", default=None, help="run every question with this split tag")
    parser.add_argument("--person", default=None, help="person UUID (omit = unbound probe)")
    parser.add_argument("--org", default=str(_DEV_INGEST_ORG), help="org UUID")
    parser.add_argument("--model-params", default=None, help="JSON dict of sampling params")
    parser.add_argument("--max-turns", type=int, default=None, help="tool-turn cap override")
    parser.add_argument("--parallel", type=int, default=4, help="concurrent questions")
    parser.add_argument("--no-cache", action="store_true", help="force fresh answers")
    parser.add_argument("--artifacts", default=None, help="artifacts root override")
    parser.add_argument(
        "--prompt-suffix", default=None,
        help="text appended to the system prompt (arm calibration; hashed into config)",
    )
    parser.add_argument(
        "--arm", choices=["generalist", "routed", "xiyan", "xiyan-routed"],
        default="generalist",
        help="agent architecture: monolithic, router+specialists, +SQL tool, or "
        "routed-SQL pipeline (set-shaped classes bypass the agent)",
    )

    args = parser.parse_args(argv)

    question_path = Path(args.questions)
    payload = json.loads(question_path.read_text(encoding="utf-8"))
    qids = {q.strip() for q in args.qids.split(",")} if args.qids else None
    questions = _select_questions(payload, qids, args.split)
    if not questions:
        raise SystemExit("No questions selected.")

    model_params: dict[str, Any] = json.loads(args.model_params) if args.model_params else {}
    pinned_now = date.fromisoformat(payload.get("pinned_now", "2026-07-04"))
    client = TogetherChatClient()
    registry = build_shared_core_registry()
    if args.arm == "xiyan":
        from app.ask.tools.sql_tool import register_sql_tool

        registry = register_sql_tool(registry)
    runner = AskAgentRunner(
        client, registry, today=pinned_now,
        max_turns=args.max_turns, model_params=model_params,
    )
    if args.prompt_suffix:
        runner._system_prompt = runner._system_prompt + "\n\n" + args.prompt_suffix  # noqa: SLF001
    # Hash the runner's EFFECTIVE params (defaults included), not the raw CLI value —
    # otherwise a changed default silently serves stale cached answers.
    effective_params = runner._model_params  # noqa: SLF001 — reproducibility pin
    config_hash = _config_hash(
        registry, runner, client.model, {**effective_params, "__arm": args.arm}
    )

    root = _artifacts_root(args.artifacts)
    cache_dir = root / "cache" / config_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    person_id = UUID(args.person) if args.person else None

    started = time.monotonic()
    records = asyncio.run(
        _answer_all(
            runner, UUID(args.org), person_id, questions,
            cache_dir, not args.no_cache, args.parallel,
            arm=args.arm, client=client,
        )
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / "runs" / f"{stamp}_{args.label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as fh:
        for record in sorted(records, key=lambda r: r["qid"]):
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    fresh = [r for r in records if not r["cached"]]
    meta = {
        "label": args.label,
        "config_hash": config_hash,
        "model": client.model,
        "model_params": effective_params,
        "max_turns": runner._max_turns,  # noqa: SLF001 — reproducibility pin
        "tools": registry.names(),
        "question_file": str(question_path.resolve()),
        "question_file_sha256": hashlib.sha256(question_path.read_bytes()).hexdigest()[:16],
        "org": args.org,
        "person": args.person,
        "pinned_now": pinned_now.isoformat(),
        "questions_run": len(records),
        "cache_hits": len(records) - len(fresh),
        "fresh_prompt_tokens": sum(r["prompt_tokens"] for r in fresh),
        "fresh_completion_tokens": sum(r["completion_tokens"] for r in fresh),
        "wall_seconds": round(time.monotonic() - started, 1),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(meta, indent=1))
    print(f"results: {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
