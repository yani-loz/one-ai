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
_TRIM_MIN_STRING = 80  # strings shorter than this are control text, never worth shrinking
# The one field whose rows are redundant by construction: `results` is a ranked PAGE of a set
# that `listing` enumerates in full, so it is spent before anything else (see _fit_payload).
_SAMPLE_FIELD = "results"


def _serialized_size(value: Any) -> int:
    """Serialized character cost of one field value (what trimming it can actually recover)."""
    return len(json.dumps(value, default=str, ensure_ascii=False))


def _fit_payload(result: Any) -> str:
    """Serialize a tool result within the observation budget WITHOUT corrupting it.

    The old blind string slice cut mid-JSON: the model was promised listing/listing_complete
    by the tool contract, then handed a payload where exactly those trailing fields were cut
    off — and the observation wasn't parseable at all.

    Two rules decide what gives way, in this order:

    1. THE SAMPLE GOES BEFORE THE ENUMERATION. `results` is a ranked PAGE of a match set that
       `listing` enumerates completely, so its rows are the redundant ones and it is spent
       first. Pure size-ordering got this backwards: on a 40-match search the listing is the
       biggest field, so it was chosen — and the whole enumeration vanished while the
       redundant page sat untouched.
    2. OTHERWISE, LARGEST FIRST. Choosing by key name emptied get_email's small `recipients`
       and `attachments` lists — both promised complete — to protect one giant `body_text`
       that then had to be shortened anyway.

    No round may take more than HALF of a field: charging the entire overshoot to whichever
    field is currently largest is what let one round wipe a 50-row listing outright. Halving
    converges in a handful of rounds and always leaves something to negotiate with.

    Control fields (totals, date_span, listing_complete, notes) are scalars, cost almost
    nothing, and therefore always survive. Every trim records a `truncated` marker; a cut
    `listing` flips listing_complete to False and a shortened `text` moves `next_offset` back
    to the real end of what the model saw, so no envelope ever overstates itself.
    """
    payload = json.dumps(result, default=str, ensure_ascii=False)
    if len(payload) <= _TOOL_RESULT_CHAR_CAP or not isinstance(result, dict):
        # Non-dict results have no field structure to trim — the hard slice is all there is.
        return (
            payload
            if len(payload) <= _TOOL_RESULT_CHAR_CAP
            else payload[:_TOOL_RESULT_CHAR_CAP] + "... [truncated]"
        )
    trimmed: dict[str, Any] = dict(result)
    dropped: dict[str, int] = {}
    shortened: list[str] = []
    while len(payload) > _TOOL_RESULT_CHAR_CAP:
        candidates = [
            (_serialized_size(v), k)
            for k, v in trimmed.items()
            if k != "truncated"
            and ((isinstance(v, list) and v) or (isinstance(v, str) and len(v) > _TRIM_MIN_STRING))
        ]
        if not candidates:
            break
        # Rule 1: spend the redundant ranked page before the complete enumeration.
        sample = [c for c in candidates if c[1] == _SAMPLE_FIELD]
        _, key = max(sample or candidates)
        value = trimmed[key]
        if isinstance(value, list):
            # Drop the tail in proportion to the overshoot, but NEVER more than half the rows
            # in one round: one round taking the whole overshoot emptied a 50-row listing
            # while another field still had 5,450 chars to give. One row minimum, so a
            # single-row field still makes progress.
            overshoot = len(payload) - _TOOL_RESULT_CHAR_CAP
            row_cost = max(1, (_serialized_size(value) - 2) // len(value))
            wanted = max(1, overshoot // row_cost + 1)
            drop = min(wanted, max(1, len(value) // 2))
            trimmed[key] = value[:-drop]
            dropped[key] = dropped.get(key, 0) + drop
            if key == "listing" and trimmed.get("listing_complete") is True:
                trimmed["listing_complete"] = False  # a cut listing is no longer complete
        else:
            overshoot = len(payload) - _TOOL_RESULT_CHAR_CAP
            keep = max(0, len(value) - overshoot - 40)
            trimmed[key] = value[:keep] + "... [truncated]"
            if key not in shortened:
                shortened.append(key)
            # get_attachment pages on next_offset; if the text was shortened the cursor must
            # follow it, otherwise the next call starts past characters the model never saw.
            if key == "text" and isinstance(trimmed.get("next_offset"), int):
                trimmed["next_offset"] = int(trimmed.get("offset") or 0) + keep
        trimmed["truncated"] = {
            "dropped_rows": dict(dropped),
            "shortened_fields": list(shortened),
            "note": "content trimmed to fit the payload budget — totals and counts above "
                    "still describe the FULL result",
        }
        payload = json.dumps(trimmed, default=str, ensure_ascii=False)
    if len(payload) > _TOOL_RESULT_CHAR_CAP:
        # Nothing left that this function knows how to shrink — a result made of many small
        # scalar or dict fields, for instance. Slicing the JSON here is what the whole rewrite
        # exists to avoid, so emit a VALID envelope that says so instead: an observation the
        # model cannot parse is worse than one that admits it lost the detail.
        # Keep the SCALARS — totals, flags, notes: they are the answer to most questions and
        # cost almost nothing. Dropping the whole envelope threw away the very count the model
        # had asked for whenever the bulk happened to sit in a dict field this loop cannot
        # shrink, which contradicts the promise that control fields always survive.
        kept: dict[str, Any] = {
            key: value
            for key, value in trimmed.items()
            if isinstance(value, (int, float, bool)) or value is None
            or (isinstance(value, str) and len(value) <= _TRIM_MIN_STRING * 4)
        }
        kept["truncated"] = {
            "note": "the bulk of this result could not be serialized within the observation "
                    "budget; the fields above are complete, the rest were dropped — narrow "
                    "the request (fewer rows, a tighter filter) and retry",
            "dropped_fields": sorted(set(trimmed) - set(kept)),
        }
        payload = json.dumps(kept, default=str, ensure_ascii=False)
        if len(payload) > _TOOL_RESULT_CHAR_CAP:  # even the scalars do not fit
            payload = json.dumps(
                {"truncated": {"note": "result too large to serialize; narrow the request"}},
                ensure_ascii=False,
            )
    return payload


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
        # Per-question usage is attributed via a per-run SINK, never by differencing the
        # client's shared cumulative counter — concurrent questions on one client would land
        # each other's tokens inside the before/after window (cross-vendor N11; measured
        # up-to-parallelism-factor over-count).
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
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
                messages, llm_tools, extra_params=self._model_params, usage_sink=usage
            )
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                answer = str(message.get("content") or "").strip()
                if not answer:
                    # Reasoning-only turn (thinking consumed the budget without a final text).
                    # One explicit nudge; a second empty reply stands and grades as-is.
                    messages.append({"role": "user", "content": "Give your final answer now."})
                    final = await self._client.chat(
                        messages, tools=None, extra_params=self._model_params,
                        usage_sink=usage,
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
            final = await self._client.chat(
                messages, tools=None, extra_params=self._model_params, usage_sink=usage
            )
            answer = str(final.get("content") or "").strip()

        return AskResult(
            question=question,
            answer=answer,
            turns=turn,
            hit_turn_cap=hit_cap,
            tool_calls=records,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
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
            # `arguments` arrives as a JSON STRING from the OpenAI-shaped wire format, but a
            # vendor (or a future adapter) may hand it over already parsed. Assuming the string
            # made json.loads raise TypeError — which is not caught below — and one such call
            # ended the whole question.
            raw_arguments = function.get("arguments")
            arguments = (
                raw_arguments
                if isinstance(raw_arguments, dict)
                else json.loads(raw_arguments or "{}")
            )
            if not isinstance(arguments, dict):
                raise ToolExecutionError("Tool arguments must be a JSON object.")
            result = await self._registry.dispatch(session, name, arguments)
            payload = _fit_payload(result)  # budget-aware, never corrupts the JSON
            ok = True
        except (json.JSONDecodeError, TypeError, UnknownToolError, ToolExecutionError) as exc:
            arguments = {}
            # Error text is model-supplied in part (an echoed tool name / bad JSON) — budget
            # it like any other observation rather than trusting it to be short.
            payload = _fit_payload({"error": str(exc)})
            ok = False
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
