"""
Role: Typed exceptions for the Ask layer — configuration, tool-dispatch, and reader-model
      failures get distinct types so the agent runner and eval harness can react per class
      (retry a model error, surface a tool error to the model as an observation, abort on
      misconfiguration).
Used by: app.ask.adapters.together_chat, app.ask.tools.registry, app.ask.services.agent_runner.
Depends on: nothing beyond the standard library.
Key invariants: never raise bare Exception in app.ask — one of these, always.
"""

from __future__ import annotations


class AskError(Exception):
    """Base class for all Ask-layer errors."""


class AskConfigurationError(AskError):
    """The Ask layer is not configured (e.g. TOGETHER_API_KEY missing) — abort, don't retry."""


class UnknownToolError(AskError):
    """The model called a tool name that is not in the registry (surfaced back as observation)."""


class ToolExecutionError(AskError):
    """A registered tool failed while executing (bad arguments, SQL error) — surfaced back to
    the model as an error observation so it can repair, never silently swallowed."""


class ReaderModelError(AskError):
    """The reader-model API call failed after bounded retries (network, 5xx, malformed reply)."""
