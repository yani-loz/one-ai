"""
Role: The query_database tool — delegates one natural-language data request to a LOCAL
      text-to-SQL specialist (XiYanSQL via Ollama), validates the generated statement through
      the PF-FBP-8 guard, executes it on the caller's READER session, and returns rows plus
      the executed SQL (transparency + repairability).
Used by: app.ask.tools registry when the xiyan arm is active (scripts/ask_loop run_eval).
Depends on: app.ask.tools.sql_guard (the validator — every statement passes it), httpx
      (local Ollama), the reader-plane session (RLS + visibility enforced server-side).
Key invariants:
  - The SQL model runs LOCALLY (sovereign; corpus schema never leaves the machine) and is
    pinned by tag — a different SQL model is a new arm.
  - The M-Schema below describes only reader-visible tables, generically (no corpus/question
    literals — anti-bias rule applies to schema descriptions too).
  - Result payloads include id columns whenever present so answers stay citable.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.registry import ToolRegistry, ToolSpec
from app.ask.tools.sql_guard import validate_generated_sql

XIYAN_MODEL = "hf.co/mradermacher/XiYanSQL-QwenCoder-7B-2504-GGUF:Q4_K_M"
_OLLAMA_URL = "http://localhost:11434/api/generate"
_MAX_ROWS = 50

_M_SCHEMA = """【DB_ID】 company_mail
【Schema】
# Table: email_message  (one row per ingested email)
[
(id:UUID, Primary Key, the message id — include in results for citation),
(from_address:VARCHAR, sender email address),
(from_name:VARCHAR, sender display name),
(subject:TEXT),
(body_text:TEXT, plain-text body),
(sent_at:TIMESTAMPTZ, when sent),
(direction:VARCHAR, 'inbound' or 'outbound'),
(is_reply:BOOLEAN),
(has_attachments:BOOLEAN)
]
# Table: email_recipient  (to/cc lines; one row per recipient per email)
[
(id:UUID, Primary Key),
(email_id:UUID, references email_message.id),
(kind:VARCHAR, 'to' or 'cc'),
(name:VARCHAR, recipient display name),
(address:VARCHAR, recipient email address)
]
# Table: email_attachment  (one row per attachment)
[
(id:UUID, Primary Key),
(email_id:UUID, references email_message.id),
(filename:VARCHAR),
(content_type:VARCHAR),
(size_bytes:INTEGER),
(extracted_text:TEXT, extracted document text when available)
]
# Table: person  (people seen in communications)
[
(id:UUID, Primary Key),
(display_name:VARCHAR),
(is_internal:BOOLEAN, member of the organization),
(first_seen_at:TIMESTAMPTZ),
(last_seen_at:TIMESTAMPTZ)
]
# Table: person_email  (addresses bound to a person)
[
(id:UUID, Primary Key),
(person_id:UUID, references person.id),
(email:VARCHAR)
]
# Table: counterparty_summary  (VIEW: per sender/recipient email domain rollup)
[
(domain:VARCHAR, counterparty email domain),
(first_contact:TIMESTAMPTZ),
(last_contact:TIMESTAMPTZ),
(first_message_id:UUID, earliest message — citable),
(last_message_id:UUID, latest message — citable),
(inbound_count:BIGINT),
(outbound_count:BIGINT),
(total_mentions:BIGINT),
(distinct_addresses:BIGINT)
]"""

_PROMPT_TEMPLATE = (
    "You are now a PostgreSQL data analyst, and you are given a database schema as "
    "follows:\n\n{schema}\n\n【Question】\n{question}\n\n【Evidence】\n"
    "Text matching must be case-insensitive (use ILIKE). Content may be written in more "
    "than one language — match the literal terms given in the question. Include id "
    "columns in the result when returning specific rows.\n\n"
    "Please read and understand the database schema carefully, and generate an executable "
    "SQL based on the user's question and evidence. The generated SQL is protected by "
    "```sql and ```.\n"
)

_SQL_FENCE = re.compile(r"```sql\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)


async def _generate_sql(request: str) -> str:
    """One local XiYanSQL call: NL request -> raw SQL text (fenced or bare)."""
    prompt = _PROMPT_TEMPLATE.format(schema=_M_SCHEMA, question=request)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            _OLLAMA_URL,
            json={
                "model": XIYAN_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": 4096},
            },
        )
    if response.status_code != 200:
        raise ToolExecutionError(f"SQL model unavailable (HTTP {response.status_code}).")
    raw = str(response.json().get("response") or "")
    match = _SQL_FENCE.search(raw)
    return (match.group(1) if match else raw).strip()


async def _query_database(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    """NL request -> generated SQL -> PF-FBP-8 guard -> reader-plane execution -> rows."""
    request = str(args.get("request") or "").strip()
    if not request:
        raise ToolExecutionError("request is required.")
    generated = await _generate_sql(request)
    safe_sql = validate_generated_sql(generated)
    try:
        result = await session.execute(text(safe_sql))
        rows = [dict(r) for r in result.mappings().fetchmany(_MAX_ROWS)]
    except Exception as exc:
        raise ToolExecutionError(
            f"Generated SQL failed to execute ({type(exc).__name__}) — rephrase the "
            "request more concretely."
        ) from exc
    return {"sql": safe_sql, "row_count": len(rows), "rows": rows}


def register_sql_tool(registry: ToolRegistry) -> ToolRegistry:
    """Add query_database to an existing registry (the xiyan arm)."""
    registry.register(
        ToolSpec(
            name="query_database",
            description=(
                "Answer a data question by DIRECT database query — a SQL specialist "
                "translates your natural-language request into one SQL statement over the "
                "email archive and returns the rows. Best for counting, ranking, grouping, "
                "listing, and date-window questions ('how many…', 'top N…', 'list all… "
                "with counts', 'earliest/latest…'). State the request precisely, including "
                "any literal search terms in the language they appear in the data."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": (
                            "Precise natural-language description of the data needed, "
                            "including filters, grouping, ordering, and literal terms."
                        ),
                    }
                },
                "required": ["request"],
            },
            executor=_query_database,
        )
    )
    return registry
