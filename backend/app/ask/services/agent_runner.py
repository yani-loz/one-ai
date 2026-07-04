"""
Role: The bounded agentic loop of the Ask layer — feeds a question plus the tool registry to
      the reader model, executes its tool calls on a reader-plane session, and returns the
      final grounded answer with a full per-turn transcript (the eval loop's atom).
Used by: scripts/ask_loop harness (eval runs); Ask API routes in a later phase.
Depends on: app.ask.adapters.together_chat, app.ask.tools.registry, app.core.config,
      app.ask.exceptions.
Key invariants:
  - BOUNDED: at most `ask_max_tool_turns` model turns with tools; hitting the cap forces a
    final tools-off answer (graded as-is, never excused) — mirrors the production posture.
  - The DB session is caller-provided and MUST come from core.database.reader_session — the
    runner adds no tenant logic and cannot widen scope (the role can't write or bypass RLS).
  - Tool failures become error observations the model can repair from, never crashes; only
    reader-model/API failures abort the run.
  - The system prompt is corpus-agnostic (no entities/domains from any question set) — its
    wording is loop-mutable but the anti-bias rule is not.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.exceptions import ToolExecutionError, UnknownToolError
from app.ask.tools.registry import ToolRegistry
from app.core.config import get_settings

_TOOL_RESULT_CHAR_CAP = 6000  # payload budget per observation (loop-mutable)

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a company-memory assistant. You answer questions using ONLY the organization's "
    "ingested email archive, reached through the tools provided — never from prior "
    "knowledge.\n\n"
    "Rules:\n"
    "- Ground every claim in tool results. REQUIRED OUTPUT FORMAT: every factual statement "
    "in your final answer MUST carry an inline citation of the supporting record id, exactly "
    "as [id: <uuid>], using ids returned by the tools. An answer that states facts without "
    "[id: ...] citations is INVALID — if you cannot cite it, do not claim it.\n"
    "- If the archive contains no data to answer, say exactly that — do not guess or invent.\n"
    "- If a name or reference is ambiguous (several matching people/threads), present the "
    "candidates instead of silently picking one.\n"
    "- Prefer counting tools for any 'how many' question; never count listed results "
    "yourself.\n"
    "- The archive may contain content in any language, not necessarily the language of "
    "the question. Choose search keywords in the language the content is likely written "
    "in, and when a search returns nothing, retry with translations or transliterations "
    "of names and terms.\n"
    "- Be concise: answer first, evidence citations inline.\n\n"
    "Today's date is {today}."
)


@dataclass
class ToolCallRecord:
    """One executed (or failed) tool call inside a run — the transcript atom.

    `result_payload` is EXACTLY the (truncated) observation string handed to the model —
    citation grading checks cited ids against it, so it must never be shortened further.
    """

    turn: int
    name: str
    arguments: dict[str, Any]
    ok: bool
    result_payload: str
    latency_ms: int


@dataclass
class AskResult:
    """The outcome of one question run: answer + everything needed to grade and diagnose it."""

    question: str
    answer: str
    turns: int
    hit_turn_cap: bool
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_seconds: float = 0.0


class AskAgentRunner:
    """Runs one question through the bounded tool-calling loop against a reader session."""

    def __init__(
        self,
        client: TogetherChatClient,
        registry: ToolRegistry,
        *,
        today: date,
        max_turns: int | None = None,
        model_params: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        """Bind the reader-model client, the tool registry, and run parameters.

        `today` is explicit (not read from the clock) so eval runs are reproducible against
        a pinned 'now'. `model_params` passes Together sampling/params overrides — model
        identity itself stays pinned in settings.
        """
        self._client = client
        self._registry = registry
        self._max_turns = max_turns or get_settings().ask_max_tool_turns
        # max_tokens must cover the reader's REASONING budget too (Together serves Qwen3.5
        # with a separate `reasoning` channel that consumes completion tokens BEFORE content —
        # 1024 measurably truncates to empty answers on multi-payload turns).
        self._model_params = {"max_tokens": 4096, **(model_params or {})}
        self._system_prompt = system_prompt or _SYSTEM_PROMPT_TEMPLATE.format(
            today=today.isoformat()
        )

    async def run(self, question: str, session: AsyncSession) -> AskResult:
        """Answer one question; return the grounded answer + full transcript.

        The loop: model turn → execute tool_calls (each observation appended) → repeat,
        until the model answers in text or the turn cap forces a tools-off final answer.
        """
        started = time.monotonic()
        usage_before = dict(self._client.total_usage)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": question},
        ]
        records: list[ToolCallRecord] = []
        llm_tools = self._registry.llm_tools()

        turn = 0
        answer: str | None = None
        hit_cap = False
        while turn < self._max_turns:
            turn += 1
            message = await self._client.chat(
                messages, llm_tools, extra_params=self._model_params
            )
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = str(message.get("content") or "").strip()
                if not answer:
                    # Reasoning-only turn (thinking consumed the budget without a final text).
                    # One explicit nudge; a second empty reply stands and grades as-is.
                    messages.append({"role": "user", "content": "Give your final answer now."})
                    final = await self._client.chat(
                        messages, tools=None, extra_params=self._model_params
                    )
                    answer = str(final.get("content") or "").strip()
                break
            messages.append(message)
            for call in tool_calls:
                messages.append(await self._execute_call(session, call, turn, records))

        if answer is None:
            hit_cap = True
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You have used all available tool calls. Answer the question now "
                        "using only what you already retrieved; if that is insufficient, "
                        "state clearly what data is missing."
                    ),
                }
            )
            final = await self._client.chat(messages, tools=None, extra_params=self._model_params)
            answer = str(final.get("content") or "").strip()

        usage_after = self._client.total_usage
        return AskResult(
            question=question,
            answer=answer,
            turns=turn,
            hit_turn_cap=hit_cap,
            tool_calls=records,
            prompt_tokens=usage_after["prompt_tokens"] - usage_before["prompt_tokens"],
            completion_tokens=usage_after["completion_tokens"]
            - usage_before["completion_tokens"],
            latency_seconds=round(time.monotonic() - started, 3),
        )

    async def _execute_call(
        self,
        session: AsyncSession,
        call: dict[str, Any],
        turn: int,
        records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        """Execute one model tool call; return the observation message (result or error)."""
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        call_started = time.monotonic()
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ToolExecutionError("Tool arguments must be a JSON object.")
            result = await self._registry.dispatch(session, name, arguments)
            payload = json.dumps(result, default=str, ensure_ascii=False)
            ok = True
        except (json.JSONDecodeError, UnknownToolError, ToolExecutionError) as exc:
            arguments = {}
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            ok = False
        if len(payload) > _TOOL_RESULT_CHAR_CAP:
            payload = payload[:_TOOL_RESULT_CHAR_CAP] + '... [truncated]"'
        records.append(
            ToolCallRecord(
                turn=turn,
                name=name,
                arguments=arguments,
                ok=ok,
                result_payload=payload,
                latency_ms=int((time.monotonic() - call_started) * 1000),
            )
        )
        return {
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": payload,
        }
