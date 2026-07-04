"""
Role: The Ask layer — permission-faithful agentic retrieval over the Connect memory. A small
      reader LLM answers natural-language questions by calling typed retrieval tools; every
      tool executes on the PF-01 reader plane (core.database.reader_session — SELECT-only role,
      org RLS + person-scoped visibility policies), so the agent physically cannot read outside
      the asking person's grants nor write anything.
Used by: the ask-tools optimization loop (scripts/ask_loop harness) today; the Ask API routes
      in a later phase.
Depends on: app.core (config, database reader seam), the Connect content tables (read-only).
Key invariants:
  - READER PLANE ONLY: no module under app.ask may import the tenant/global/owner engines or
    session factories — reader_session is the single DB seam (AST-testable, mirrors app.access).
  - NO benchmark literals: tool code, SQL, descriptions, and prompts must never name specific
    people/companies/dates from any gold question set (the loop's anti-bias rule, verifier-audited).
  - The Together adapter (adapters/) is the ONLY file that knows the LLM wire format.
"""
