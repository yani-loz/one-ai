"""
Role: The tool registry — the single place where the Ask agent's callable tools are declared,
      serialized for the LLM (OpenAI-compatible `tools` array), and dispatched by name. The
      registry IS the mutable surface of the ask-tools optimization loop: iterations add,
      remove, and reshape ToolSpecs here (or in kit modules that register into it).
Used by: app.ask.services.agent_runner (serialization + dispatch), scripts/ask_loop harness.
Depends on: app.ask.exceptions; tool executor modules (e.g. tools.shared_core) register in.
Key invariants:
  - Executors receive a READER-plane AsyncSession (caller-provided) + validated JSON args;
    they return JSON-serializable data and never mutate anything (the role can't anyway).
  - Tool names are snake_case verbs, descriptions are corpus-agnostic (no benchmark literals).
  - Dispatch failures come back as UnknownToolError/ToolExecutionError — the runner turns them
    into error observations for the model (repair beats crash for a weak reader).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError, UnknownToolError

ToolExecutor = Callable[[AsyncSession, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """One callable tool: LLM-facing contract + server-side executor.

    `parameters` is a JSON-Schema object (type/properties/required) exactly as serialized
    into the LLM `tools` array — its wording is part of the loop's mutation space.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    executor: ToolExecutor


class ToolRegistry:
    """Named collection of ToolSpecs with LLM serialization and safe dispatch."""

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        """Start empty or from an initial spec list (duplicate names refuse loudly)."""
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        """Add one tool; a duplicate name is a wiring bug, not an override."""
        if spec.name in self._specs:
            raise ToolExecutionError(f"Tool {spec.name!r} is already registered.")
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        """Registered tool names (stable order for ledger pins)."""
        return sorted(self._specs)

    def llm_tools(self) -> list[dict[str, Any]]:
        """Serialize every tool into the OpenAI-compatible `tools` array shape."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    async def dispatch(self, session: AsyncSession, name: str, args: dict[str, Any]) -> Any:
        """Execute tool `name` with `args` on the caller's reader session.

        Raises:
            UnknownToolError: the model invented a tool name.
            ToolExecutionError: the executor failed (wraps the original error type name —
                never the raw message, which could echo query text into logs).
        """
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownToolError(
                f"Unknown tool {name!r}; available: {', '.join(self.names())}."
            )
        try:
            return await spec.executor(session, args)
        except (UnknownToolError, ToolExecutionError):
            raise
        except Exception as exc:
            raise ToolExecutionError(f"Tool {name!r} failed: {type(exc).__name__}.") from exc
