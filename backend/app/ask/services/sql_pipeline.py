"""
Role: The direct SQL answer pipeline (xiyan-routed arm, Shape B) — for set-shaped question
      classes the weak reader never delegates properly, bypass agent orchestration entirely:
      the raw question goes to the local SQL specialist, the guarded statement executes on
      the reader plane, and the reader model only COMPOSES a cited answer from the rows.
Used by: scripts/ask_loop run_eval (--arm xiyan-routed).
Depends on: app.ask.tools.sql_tool (_generate_sql + guard), app.ask.adapters.together_chat.
Key invariants:
  - FALL-THROUGH, never worse: SQL generation/validation/execution failure or zero rows
    returns None and the caller runs the normal agent — this pipeline can only add.
  - Composition is grounded: the composer sees ONLY question + SQL + rows and must cite row
    ids; it cannot call tools, so it cannot wander.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.tools.sql_guard import validate_generated_sql
from app.ask.tools.sql_tool import _generate_sql  # noqa: PLC2701 — same package seam

SQL_PIPELINE_CLASSES = frozenset({"aggregation", "temporal_activity"})
_MAX_ROWS = 50

_COMPOSE_PROMPT = (
    "You are a company-memory assistant. A database query was executed to answer the "
    "user's question. Compose the final answer USING ONLY the rows below.\n"
    "- Cite row ids in the form [id: <uuid>] where id columns are present.\n"
    "- If the rows cannot answer the question, say exactly what is missing — do not guess.\n"
    "- Be concise: answer first.\n\n"
    "Question: {question}\n\nSQL executed:\n{sql}\n\nRows ({row_count}):\n{rows}\n"
)


async def answer_via_sql(
    client: TogetherChatClient, session: AsyncSession, question: str
) -> dict[str, Any] | None:
    """Try the direct-SQL path; return a result record or None to fall through.

    Record shape mirrors what run_eval stores: answer, turns (1), tool_calls (one
    synthetic query_database entry so citation grading sees the payload), token/latency.
    """
    started = time.monotonic()
    usage_before = dict(client.total_usage)
    try:
        generated = await _generate_sql(question)
        safe_sql = validate_generated_sql(generated)
        result = await session.execute(text(safe_sql))
        rows = [dict(r) for r in result.mappings().fetchmany(_MAX_ROWS)]
    except Exception:  # noqa: BLE001 — fall through to the agent on ANY pipeline failure
        return None
    if not rows:
        return None

    payload = json.dumps({"sql": safe_sql, "rows": rows}, default=str, ensure_ascii=False)
    compose_messages = [
        {
            "role": "user",
            "content": _COMPOSE_PROMPT.format(
                question=question,
                sql=safe_sql,
                row_count=len(rows),
                rows=json.dumps(rows, default=str, ensure_ascii=False)[:6000],
            ),
        }
    ]
    compose = await client.chat(compose_messages, tools=None, max_tokens=4096)
    answer = str(compose.get("content") or "").strip()
    if not answer:
        # Reasoning-only turn (measured across every call site) — one explicit nudge.
        compose_messages.append({"role": "user", "content": "Give your final answer now."})
        retry = await client.chat(compose_messages, tools=None, max_tokens=4096)
        answer = str(retry.get("content") or "").strip()
    if not answer:
        return None
    usage_after = client.total_usage
    return {
        "answer": answer,
        "turns": 1,
        "hit_turn_cap": False,
        "tool_calls": [
            {
                "turn": 1,
                "name": "query_database",
                "arguments": {"request": question},
                "ok": True,
                "result_payload": payload[:6000],
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        ],
        "prompt_tokens": usage_after["prompt_tokens"] - usage_before["prompt_tokens"],
        "completion_tokens": usage_after["completion_tokens"]
        - usage_before["completion_tokens"],
        "latency_seconds": round(time.monotonic() - started, 3),
    }
