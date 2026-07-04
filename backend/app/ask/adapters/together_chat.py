"""
Role: Together AI chat-completions adapter — the vendor boundary for the Ask reader model.
      Wraps POST {base_url}/chat/completions (OpenAI-compatible: messages + tools in,
      assistant message with optional tool_calls out) with bounded retries and cumulative
      token-usage tracking. Tests mock THIS class, never the HTTP internals of callers.
Used by: app.ask.services.agent_runner (the agentic loop).
Depends on: httpx, app.core.config (key/model/base-url/timeout), app.ask.exceptions.
Key invariants:
  - The API key is read from settings once, sent only as the Authorization header, and NEVER
    logged, printed, or included in any raised error message.
  - temperature defaults to 0 (the eval loop's determinism requirement); callers may pass
    sampling/params overrides but the MODEL IDENTITY comes from settings alone — a different
    model is a config/arm decision, not a call-site parameter.
  - Retries: 429 and 5xx retry with exponential backoff up to _MAX_ATTEMPTS; 4xx (except 429)
    fail immediately — they are caller bugs, not transient faults.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.ask.exceptions import AskConfigurationError, ReaderModelError
from app.core.config import get_settings

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 2.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class TogetherChatClient:
    """Async client for Together chat completions with usage accounting.

    One instance per eval run / request context; `total_usage` accumulates the token counts
    of every successful call so the harness can report spend per question and per iteration.
    """

    def __init__(self) -> None:
        """Read connection settings; fail fast if the API key is absent."""
        settings = get_settings()
        if not settings.together_api_key:
            raise AskConfigurationError(
                "TOGETHER_API_KEY is not set — the Ask reader model cannot be reached."
            )
        self._base_url = settings.ask_reader_base_url.rstrip("/")
        self._model = settings.ask_reader_model
        self._timeout = settings.ask_reader_timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {settings.together_api_key}",
            "Content-Type": "application/json",
        }
        self.total_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    @property
    def model(self) -> str:
        """The pinned model identity (for ledger/reproducibility pins)."""
        return self._model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one chat-completion call; return the assistant message dict.

        Contract: returns the raw `choices[0].message` object (keys: role, content,
        optionally tool_calls) and updates `total_usage`. `extra_params` passes model
        parameters (top_p, top_k, repetition_penalty, ...) — mutable in the loop; the
        model name itself is NOT overridable here.

        Raises:
            ReaderModelError: transport failure, retryable status exhausted, or a
                response missing `choices`.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if extra_params:
            payload.update(extra_params)

        last_error = "unknown"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers,
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    last_error = f"transport: {type(exc).__name__}"
                else:
                    if response.status_code == 200:
                        return self._extract_message(response.json())
                    last_error = f"HTTP {response.status_code}"
                    if response.status_code not in _RETRYABLE_STATUS:
                        raise ReaderModelError(
                            f"Reader model call failed ({last_error}) — not retryable."
                        )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise ReaderModelError(
            f"Reader model call failed after {_MAX_ATTEMPTS} attempts (last: {last_error})."
        )

    def _extract_message(self, body: dict[str, Any]) -> dict[str, Any]:
        """Pull choices[0].message out of a success body and accumulate usage."""
        usage = body.get("usage") or {}
        self.total_usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.total_usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        choices = body.get("choices")
        if not choices or "message" not in choices[0]:
            raise ReaderModelError("Reader model returned a body without choices[0].message.")
        return choices[0]["message"]
