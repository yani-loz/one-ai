"""
Role: The Ask intent router + per-class specialist kits (the router+specialists arm).
      One cheap classification call maps a question to one of the six universal intent
      classes (docs/PM/ask/intent-classes.md); each class gets a scoped tool subset and a
      class-specific procedure block appended to the system prompt. Low confidence or
      parse failure falls back to the generalist (full tools, no addendum) — never a guess.
Used by: scripts/ask_loop run_eval (--arm routed); Ask API routes later.
Depends on: app.ask.adapters.together_chat, app.ask.tools.registry/shared_core.
Key invariants:
  - Class definitions and procedures are corpus-agnostic (universal for any tenant) —
    no entities/domains/dates from any question set (anti-bias rule, verifier-audited).
  - The router NEVER blocks answering: any failure routes to the generalist.
  - Tool subsets only ever REMOVE tools from the shared registry (no class-secret tools).
"""

from __future__ import annotations

from typing import Any

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.tools.registry import ToolRegistry, ToolSpec

INTENT_CLASSES: dict[str, dict[str, Any]] = {
    "entity_lookup": {
        "definition": "identify a person or organization: who they are, contact details, "
        "addresses, affiliations",
        "tools": ["find_person", "get_counterparty_summary", "search_emails", "get_email"],
        "procedure": (
            "Procedure: find_person for people (try transliterations); "
            "get_counterparty_summary for organizations; then read 1-2 key messages with "
            "get_email to extract roles/titles from signatures before answering."
        ),
    },
    "content_search": {
        "definition": "find specific emails, documents, or attachments by topic, "
        "participant, type, or date",
        "tools": ["search_emails", "search_attachments", "get_email", "count_emails"],
        "procedure": (
            "Procedure: search with several keyword variants (both languages); check "
            "total_matches vs shown; narrow by participant/date to reach buried results; "
            "open the best hits with get_email before answering."
        ),
    },
    "aggregation": {
        "definition": "count, rank, or compare volumes: how many, top-N, most/least, "
        "distribution over time",
        "tools": ["count_emails", "list_counterparties", "search_emails",
                  "get_counterparty_summary"],
        "procedure": (
            "Procedure: NEVER count search results by hand — use count_emails (with "
            "group_by for rankings) or list_counterparties. For named-entity lists, "
            "enumerate candidates first, then verify each named entry with at least one "
            "concrete message before including it; state when a list may be incomplete."
        ),
    },
    "temporal_activity": {
        "definition": "when something first/last happened, timelines, activity in a window, "
        "gaps and cadence changes",
        "tools": ["get_counterparty_summary", "search_emails", "count_emails", "get_email",
                  "find_person"],
        "procedure": (
            "Procedure: for first/last contact use get_counterparty_summary (citable "
            "first/last_message_id); communication includes BOTH directions; for windows, "
            "filter with date_from/date_to and verify boundaries against total_matches."
        ),
    },
    "synthesis": {
        "definition": "assemble a full picture, history, dossier, or status of a company, "
        "person, project, or relationship from many messages",
        "tools": ["get_counterparty_summary", "search_emails", "get_email", "find_person",
                  "search_attachments", "count_emails"],
        "procedure": (
            "Procedure: start with get_counterparty_summary for the frame (span, volumes); "
            "then read the FIRST message, the LAST message, and 2-3 pivotal ones in full "
            "with get_email; check attachments of key emails (money/terms live there); "
            "report the arc — start, key events, current state — each claim cited."
        ),
    },
    "existence_check": {
        "definition": "whether anything matching a description exists at all; verified "
        "absence is a valid answer",
        "tools": ["search_emails", "search_attachments", "count_emails", "find_person"],
        "procedure": (
            "Procedure: search multiple variants in BOTH languages before concluding "
            "absence; report which terms you tried; a confident 'no data' with the "
            "searches listed beats a hedge."
        ),
    },
}

_ROUTER_PROMPT = (
    "Classify the user question into exactly ONE intent class. Reply with ONLY the class "
    "name, nothing else.\n\nClasses:\n"
    + "\n".join(f"- {name}: {spec['definition']}" for name, spec in INTENT_CLASSES.items())
    + "\n\nQuestion: {question}"
)


async def classify_question(client: TogetherChatClient, question: str) -> str | None:
    """Route one question to an intent class; None = generalist fallback (never raises)."""
    try:
        message = await client.chat(
            [{"role": "user", "content": _ROUTER_PROMPT.replace("{question}", question)}],
            tools=None,
            max_tokens=2048,  # the reasoning channel spends tokens BEFORE content (measured)
        )
        # Prefer the CONTENT answer; fall back to the reasoning channel, where the model
        # discusses several classes before concluding — take the LAST class mentioned
        # (the conclusion), never the first (dict-order scanning misroutes, measured 12%).
        content = str(message.get("content") or "").strip().lower()
        for name in INTENT_CLASSES:
            if name in content:
                return name
        reasoning = str(message.get("reasoning") or "").lower()
        best: tuple[int, str] | None = None
        for name in INTENT_CLASSES:
            pos = reasoning.rfind(name)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, name)
        if best is not None:
            return best[1]
    except Exception:  # noqa: BLE001 — routing must never block answering
        return None
    return None


def registry_for_class(full: ToolRegistry, class_name: str | None) -> ToolRegistry:
    """Scope the shared registry to the class kit (generalist/None = full registry)."""
    if class_name is None or class_name not in INTENT_CLASSES:
        return full
    allowed = set(INTENT_CLASSES[class_name]["tools"])
    specs: list[ToolSpec] = [
        spec for name, spec in full._specs.items() if name in allowed  # noqa: SLF001
    ]
    return ToolRegistry(specs) if specs else full


def prompt_addendum_for_class(class_name: str | None) -> str:
    """The class procedure block to append to the system prompt ('' for generalist)."""
    if class_name is None or class_name not in INTENT_CLASSES:
        return ""
    return "\n\n" + str(INTENT_CLASSES[class_name]["procedure"])
