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
  - The router NEVER blocks answering: any CLASSIFICATION failure routes to the generalist.
    A kit naming tools absent from the live registry is a WIRING bug and raises loudly —
    a silent kit shrink already confounded one arm verdict (N12); never again.
  - Tool subsets only ever REMOVE tools from the shared registry (no class-secret tools).
"""

from __future__ import annotations

import re
from typing import Any

from app.ask.adapters.together_chat import TogetherChatClient
from app.ask.tools.registry import ToolRegistry

# Kits may name ONLY tools that exist in the live shared-core registry — registry_for_class
# refuses unknown names loudly. (Cross-vendor N12: the counterparty-summary tools rescinded in
# e4a4535 lingered here, silently shrinking 4 of 6 kits and pointing procedures at phantom
# tools/parameters; the routed-arm PARK verdict was measured in that crippled state.)
INTENT_CLASSES: dict[str, dict[str, Any]] = {
    "entity_lookup": {
        "definition": "identify a person or organization: who they are, contact details, "
        "addresses, affiliations",
        "tools": ["find_person", "search_emails", "get_email"],
        "procedure": (
            "Procedure: find_person for people (try transliterations); for ORGANIZATIONS "
            "pass their matching_domains to search_emails participants; then read 1-2 key "
            "messages with get_email to extract roles/titles from signatures before "
            "answering."
        ),
    },
    "content_search": {
        "definition": "find specific emails, documents, or attachments by topic, "
        "participant, type, or date",
        "tools": ["search_emails", "search_attachments", "get_attachment", "get_email",
                  "count_emails"],
        "procedure": (
            "Procedure: search with several keyword variants (both languages); check "
            "total_matches vs shown; narrow by participant/date to reach buried results; "
            "open the best hits with get_email, and read matching documents with "
            "get_attachment before answering."
        ),
    },
    "aggregation": {
        "definition": "count, rank, or compare volumes: how many, top-N, most/least, "
        "distribution over time",
        "tools": ["count_emails", "search_emails", "find_person"],
        "procedure": (
            "Procedure: NEVER count search results by hand — use count_emails. For "
            "rankings/top-N, enumerate the candidates first (find_person matching_domains, "
            "search results), then call count_emails per candidate and compare the counts. "
            "Verify each named entry with at least one concrete message before including "
            "it; state when a list may be incomplete."
        ),
    },
    "temporal_activity": {
        "definition": "when something first/last happened, timelines, activity in a window, "
        "gaps and cadence changes",
        "tools": ["search_emails", "count_emails", "get_email", "find_person"],
        "procedure": (
            "Procedure: for first/last contact use search_emails with a participant filter "
            "and read date_span / span_boundary_emails (citable ids) — never the ranked "
            "page; communication includes BOTH directions; for windows, filter with "
            "date_from/date_to and verify boundaries against total_matches."
        ),
    },
    "synthesis": {
        "definition": "assemble a full picture, history, dossier, or status of a company, "
        "person, project, or relationship from many messages",
        "tools": ["search_emails", "get_email", "find_person", "search_attachments",
                  "get_attachment", "count_emails"],
        "procedure": (
            "Procedure: start with search_emails on the counterparty (participants filter) "
            "for the frame — total_matches, date_span, span_boundary_emails; then read the "
            "FIRST message, the LAST message, and 2-3 pivotal ones in full with get_email; "
            "open key attachments with get_attachment (money/terms live INSIDE documents); "
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


def _last_mentioned_class(prose: str) -> str | None:
    """The LAST intent class mentioned in prose — the conclusion, never the first mention.

    Dict-order first-match scanning returns classes the model explicitly REJECTED ('this is
    not an entity_lookup … so synthesis' → entity_lookup) and collapsed a full real run onto
    the first dict key (measured 33/33) — cross-vendor N13. Applied to BOTH channels.
    """
    best: tuple[int, str] | None = None
    for name in INTENT_CLASSES:
        # Word-bounded: a bare rfind matched class names inside longer tokens, so prose that
        # merely restated the menu (or used a hyphenated compound) routed on the last mention
        # of a substring rather than on a class the model actually chose.
        matches = list(re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", prose))
        if matches and (best is None or matches[-1].start() > best[0]):
            best = (matches[-1].start(), name)
    return best[1] if best else None


async def classify_question(
    client: TogetherChatClient, question: str, usage_sink: dict[str, int] | None = None
) -> str | None:
    """Route one question to an intent class; None = generalist fallback (never raises).

    `usage_sink` receives this call's tokens. The routing call is a real cost of the routed
    arm; without a sink it was spent off-book, so the arm looked cheaper than it is exactly
    where the comparison against the generalist matters.
    """
    try:
        message = await client.chat(
            [{"role": "user", "content": _ROUTER_PROMPT.replace("{question}", question)}],
            tools=None,
            max_tokens=2048,  # the reasoning channel spends tokens BEFORE content (measured)
            usage_sink=usage_sink,
        )
        # Prefer the CONTENT answer: a compliant bare class name is taken verbatim; prose
        # gets the same last-mention rule as reasoning (first-match misroutes, N13).
        content = str(message.get("content") or "").strip().lower()
        if content.rstrip(".!") in INTENT_CLASSES:
            return content.rstrip(".!")
        from_content = _last_mentioned_class(content)
        if from_content is not None:
            return from_content
        return _last_mentioned_class(str(message.get("reasoning") or "").lower())
    except Exception:  # noqa: BLE001 — routing must never block answering
        return None


def registry_for_class(full: ToolRegistry, class_name: str | None) -> ToolRegistry:
    """Scope the shared registry to the class kit (generalist/None = full registry).

    A kit naming a tool the registry does not carry is a WIRING BUG and raises loudly —
    silently dropping unknown names is how the rescinded counterparty tools crippled 4 of 6
    kits without any signal (N12); a routed arm must never run on a shrunken kit again.

    Raises:
        UnknownToolError: the kit references tools absent from the live registry.
    """
    if class_name is None or class_name not in INTENT_CLASSES:
        return full
    return full.subset(set(INTENT_CLASSES[class_name]["tools"]))


def prompt_addendum_for_class(class_name: str | None) -> str:
    """The class procedure block to append to the system prompt ('' for generalist)."""
    if class_name is None or class_name not in INTENT_CLASSES:
        return ""
    return "\n\n" + str(INTENT_CLASSES[class_name]["procedure"])
