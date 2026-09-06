"""
Role: The direct SQL answer pipeline (xiyan-routed arm, Shape B) — for set-shaped question
      classes the weak reader never delegates properly, bypass agent orchestration entirely:
      the raw question goes to the local SQL specialist, the guarded statement executes on
      the reader plane, and the reader model only COMPOSES a cited answer from the rows.
Used by: scripts/ask_loop run_eval (--arm xiyan-routed).
Depends on: app.ask.tools.sql_tool (_generate_sql + guard), app.ask.adapters.together_chat.
Key invariants:
  - FALL-THROUGH, never worse: SQL generation/validation/execution failure, zero rows, or a
    COMPOSE-call failure returns None and the caller runs the normal agent — this pipeline
    can only add (N10: compose used to escape the try and kill the whole run).
  - Composition is grounded: the composer sees ONLY question + SQL + rows and must cite row
    ids; it cannot call tools, so it cannot wander.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.tools.sql_execution import execute_guarded_sql
from app.ask.tools.sql_tool import _generate_sql  # noqa: PLC2701 — same package seam
from app.ask.tools.tool_helpers import redact_uuids

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
    # Per-question attribution via a private sink, never by differencing the client's shared
    # cumulative counter: under --parallel the before/after window catches every concurrent
    # question's tokens (N11 — the same defect fixed in AskAgentRunner).
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        generated = await _generate_sql(question)
        safe_sql, rows = await execute_guarded_sql(session, generated, max_rows=_MAX_ROWS)
    except Exception:  # noqa: BLE001 — fall through to the agent on ANY pipeline failure
        return None
    if not rows:
        return None

    # ONE truncated rows string is both what the composer READS and what the record STORES
    # (cross-vendor N7): two independently-sliced windows over different strings meant the
    # stored payload always ended earlier in the row stream than what the composer saw, so
    # the grader called genuine tail-row citations invented. The stored payload embeds this
    # exact string; grading regex-scans it, so the JSON-escaped nesting is irrelevant.
    rows_json = json.dumps(rows, default=str, ensure_ascii=False)[:6000]
    # The echoed statement is redacted of uuids (only ids that came OUT of the database, in
    # `rows`, may count as citable evidence) — and the composer is shown the SAME redacted
    # text that is stored. Showing one string and storing another made a genuine citation
    # grade as fabricated, and broke the ToolCallRecord contract that result_payload is
    # exactly what the model saw.
    shown_sql = redact_uuids(safe_sql)
    payload = json.dumps({"sql": shown_sql, "rows": rows_json}, ensure_ascii=False)
    compose_messages = [
        {
            "role": "user",
            "content": _COMPOSE_PROMPT.format(
                question=question,
                sql=shown_sql,
                row_count=len(rows),
                rows=rows_json,
            ),
        }
    ]
    try:
        compose = await client.chat(
            compose_messages, tools=None, max_tokens=4096, usage_sink=usage
        )
        answer = str(compose.get("content") or "").strip()
        if not answer:
            # Reasoning-only turn (measured across every call site) — one explicit nudge.
            compose_messages.append(
                {"role": "user", "content": "Give your final answer now."}
            )
            retry = await client.chat(
                compose_messages, tools=None, max_tokens=4096, usage_sink=usage
            )
            answer = str(retry.get("content") or "").strip()
    except Exception:  # noqa: BLE001 — the 'can only add' fall-through covers compose too (N10)
        return None
    if not answer:
        return None
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
                # Already budgeted via rows_json above — never slice again (the ToolCallRecord
                # contract: result_payload is EXACTLY what the composer saw).
                "result_payload": payload,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        ],
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "latency_seconds": round(time.monotonic() - started, 3),
    }
