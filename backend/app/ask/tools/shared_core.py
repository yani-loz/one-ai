"""
Role: The v0 shared-core registry — assembles the six generic retrieval tools every Ask
      specialist gets (find_person, search_emails, count_emails, get_email,
      search_attachments, get_attachment) from their per-domain modules. This is the
      BASELINE tool set of the ask-tools loop; iterations mutate/extend from here.
Used by: scripts/ask_loop/run_eval.py (builds the registry it hands to AskAgentRunner) and
      tests/ask/services/test_router.py; app.ask.services.router consumes the resulting
      registry, and its intent kits may name only tools assembled here.
Depends on: app.ask.tools.person_tool / email_search / email_read / attachment_tools, registry.
Key invariants:
  - Every query runs on the caller's READER-plane session: org RLS + person visibility policies
    apply server-side; the org_id/person GUCs are already bound — tools add NO tenant logic.
  - Generic by construction: no entity names, domains, folder names, or date literals from any
    question set may appear in any tool module (loop anti-bias rule; verifier-audited).
  - Result payloads are compact JSON (ids, ISO dates, truncated snippets) — token economy for
    a small reader; full bodies only via get_email, document text only via get_attachment.
  - Tool NAMES are the router's contract: adding or removing one here means reconciling
    app.ask.services.router INTENT_CLASSES (which now refuses unknown names loudly).
"""

from __future__ import annotations

from app.ask.tools.attachment_tools import GET_ATTACHMENT_SPEC, SEARCH_ATTACHMENTS_SPEC
from app.ask.tools.email_read import GET_EMAIL_SPEC
from app.ask.tools.email_search import COUNT_EMAILS_SPEC, SEARCH_EMAILS_SPEC
from app.ask.tools.person_tool import FIND_PERSON_SPEC
from app.ask.tools.registry import ToolRegistry


def build_shared_core_registry() -> ToolRegistry:
    """Assemble the v0 baseline registry (the loop's parent configuration)."""
    return ToolRegistry(
        [
            FIND_PERSON_SPEC,
            SEARCH_EMAILS_SPEC,
            COUNT_EMAILS_SPEC,
            GET_EMAIL_SPEC,
            SEARCH_ATTACHMENTS_SPEC,
            GET_ATTACHMENT_SPEC,
        ]
    )
