"""
Role: The permanent record of every attack that has ever worked (or nearly worked) against the
      Ask layer, expressed as DATA. Adding a case is one entry; the tests iterate the corpus,
      so a defence can never be quietly dropped by a later refactor — the coverage lives here,
      not in the shape of some test function someone may rewrite.
Used by: tests/ask/security/test_attack_corpus.py.
Depends on: nothing (plain data — importable without a DB or any app module).
Key invariants:
  - EVERY entry cites where it came from and WHICH guarantee it protects. A case whose
    provenance nobody remembers is a case nobody dares delete and nobody trusts.
  - The ALLOWED corpus is not decoration: over-rejection is the other way this layer fails.
    A hardening pass that quietly stops answering legitimate questions is still a regression,
    and these entries are what catches it.
  - Cases describe INPUT and EXPECTED OUTCOME only. They must never encode the mechanism that
    rejects them: the whole point is that a future redesign (a different guard, a different
    checkpoint) still has to satisfy the same corpus.
"""

from __future__ import annotations

from tests.ask.security.corpus_types import AllowedCase, AttackCase, RedactedCase
from tests.ask.security.fabrication_corpus import (
    FABRICATION_ATTACKS,
    LAUNDERING_INPUTS,
    LAUNDERING_UUID,
    REDACTED_STATEMENTS,
)

# — Statements that must NEVER execute —————————————————————————————————————————————

_PLANE_ATTACKS: tuple[AttackCase, ...] = (
    AttackCase(
        case_id="plain-set-config",
        guarantee="tenant isolation + person visibility (the RLS scope must be immovable)",
        found="PF-FBP-8, the hazard the guard was built for",
        sql="SELECT set_config('app.current_org_id', 'HIJACKED', true)",
    ),
    AttackCase(
        case_id="quoted-identifier-set-config",
        guarantee="tenant isolation + person visibility",
        found="fix-wave review 2026-07-25 — a mask that blanked quoted identifiers hid it",
        sql="SELECT \"set_config\"('app.current_org_id', 'HIJACKED', true)",
    ),
    AttackCase(
        case_id="unicode-escape-set-config",
        guarantee="tenant isolation + person visibility",
        found='red-team round 1 — PostgreSQL DECODES U&"…", so the name a scan sees is not '
        "the name the server resolves; demonstrated as the restricted reader role",
        sql="SELECT U&\"\\0073et_config\"('app.current_org_id', 'HIJACKED', true)",
    ),
    AttackCase(
        case_id="phantom-dollar-quote",
        guarantee="tenant isolation (a stray $ hid the rest of the statement from every check)",
        found="fix-wave review 2026-07-25 — '$' is an identifier character in PostgreSQL",
        sql="SELECT 1 AS a$$b$$, set_config('app.current_org_id', 'HIJACKED', false) FROM t",
    ),
    AttackCase(
        case_id="second-statement-behind-phantom-quote",
        guarantee="single-statement execution",
        found="fix-wave review 2026-07-25",
        sql="SELECT a$$q$ FROM t; DROP TABLE email_message",
    ),
    AttackCase(
        case_id="hijack-read-restore-scalar-subqueries",
        guarantee="tenant isolation — and the reason a before/after check is only a tripwire",
        found="self-audit 2026-07-26 — reads another org's rows and leaves the GUC untouched",
        sql="SELECT (SELECT set_config('app.current_org_id', 'ORG-B', true)) AS hijack, "
        "(SELECT count(*) FROM email_message) AS leaked, "
        "(SELECT set_config('app.current_org_id', 'ORG-A', true)) AS restore",
    ),
    AttackCase(
        case_id="hijack-via-from-clause",
        guarantee="tenant isolation",
        found="self-audit 2026-07-26 — same trick through evaluation order in FROM",
        sql="SELECT m.id FROM (SELECT set_config('app.current_org_id', 'ORG-B', true)) "
        "AS h(x), email_message m",
    ),
    AttackCase(
        case_id="hijack-via-cte",
        guarantee="tenant isolation",
        found="self-audit 2026-07-26",
        sql="WITH h AS (SELECT set_config('app.current_org_id', 'ORG-B', true) AS x) "
        "SELECT (SELECT x FROM h) AS hijack, count(*) AS leaked FROM email_message",
    ),
    AttackCase(
        case_id="query-to-xml-executes-a-string",
        guarantee="read-only plane — the SQL payload is a string nothing parses",
        found="red-team round 1",
        sql="SELECT query_to_xml('SELECT set_config(''app.current_org_id'', ''X'', false)', "
        "true, true, '')",
    ),
    AttackCase(
        case_id="query-to-xml-in-from-clause",
        guarantee="read-only plane",
        found="self-audit 2026-07-26 — the FROM-clause form plans differently",
        sql="SELECT * FROM query_to_xml('SELECT 1', true, true, '') AS x",
    ),
    AttackCase(
        case_id="read-audit-log",
        guarantee="reach equals documentation, not the reader role's grant list",
        found="red-team round 1 — audit_log carries actor emails and IP addresses",
        sql="SELECT actor_email FROM audit_log",
    ),
    AttackCase(
        case_id="read-connector-secrets",
        guarantee="reach equals documentation",
        found="red-team round 1 — connector_connection carries mailbox usernames + ciphertext",
        sql="SELECT username, secret_ciphertext FROM connector_connection",
    ),
    AttackCase(
        case_id="read-identity-bindings",
        guarantee="reach equals documentation",
        found="red-team round 1",
        sql="SELECT count(*) FROM principal_source_identity",
    ),
    AttackCase(
        case_id="smuggle-forbidden-table-in-a-subquery",
        guarantee="reach equals documentation, including nested scopes",
        found="self-audit 2026-07-26",
        sql="SELECT (SELECT count(*) FROM audit_log) AS leaked",
    ),
    # ANCHORED to a real table on purpose. Written relationless, these were refused by the
    # "must read an allowed table" rule and never reached the function check at all — so the
    # call scan could have been deleted with the whole suite still green (round-3 armour
    # audit). A corpus case must exercise the defence it is credited to.
    AttackCase(
        case_id="database-to-xml-dump",
        guarantee="reach equals documentation",
        found="red-team round 2 — proven to dump connector credentials, the whole audit_log "
        "and the identity graph; the SPI dump is invisible to the planner",
        sql="SELECT database_to_xml(true, false, '') AS dump FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="schema-to-xml-dump",
        guarantee="reach equals documentation",
        found="red-team round 2 — the same family, one name the denylist had not enumerated",
        sql="SELECT schema_to_xml('public', true, false, '') AS dump FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="xmlschema-variant-dump",
        guarantee="reach equals documentation",
        found="red-team round 2 — enumerating this family by name missed it twice, so the "
        "corpus keeps a variant nobody listed",
        sql="SELECT database_to_xml_and_xmlschema(true, false, '') AS dump "
        "FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="relationless-dump",
        guarantee="reach equals documentation",
        found="the same dump WITHOUT a table — kept alongside the anchored form so the "
        "touches-a-relation rule and the call scan are each exercised by their own case",
        sql="SELECT database_to_xml(true, false, '') AS dump",
    ),
    AttackCase(
        case_id="ts-rewrite-spi-executor",
        guarantee="tenant isolation + person visibility + read-only plane",
        found="red-team round 3 — ts_rewrite runs its second argument as SQL through SPI and "
        "is reader-executable; demonstrated reading another org's restricted email while the "
        "scope tripwire stayed silent (move-read-restore inside one statement). THIRD name a "
        "denylist missed, which is why function checking is now an allowlist",
        sql="SELECT ts_rewrite('a'::tsquery, 'SELECT ''a''::tsquery, ''b''::tsquery')::text "
        "AS x FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="large-object-write-from-a-select",
        guarantee="read-only plane — SELECT-only is not read-only by itself",
        found="cross-vendor round 2 — lo_from_bytea WRITES a large object from a plain SELECT",
        sql="SELECT lo_from_bytea(0, decode('aa', 'hex')) AS oid FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="session-advisory-lock",
        guarantee="availability — a session-level lock outlives the savepoint that held it",
        found="cross-vendor round 2",
        sql="SELECT pg_try_advisory_lock_shared(1) AS locked FROM email_message LIMIT 1",
    ),
    AttackCase(
        case_id="forbidden-call-in-a-filter",
        guarantee="read-only plane",
        found="round-5 pass 4 — every other forbidden-call case SELECTS the call, so its output "
        "is caller-authored and the provenance rule refuses it first; the call allowlist was "
        "left with nothing proving it. Here the output is a real column and the forbidden call "
        "is in the predicate. `pg_sleep` would not do: it is in the TEXT guard's denylist, "
        "which shadows the allowlist just as thoroughly. `query_to_xml` is not.",
        sql="SELECT m.id FROM email_message m "
        "WHERE query_to_xml('SELECT 1', true, true, '') IS NOT NULL",
    ),
    AttackCase(
        case_id="filesystem-read",
        guarantee="read-only plane",
        found="PF-FBP-8 denylist",
        sql="SELECT pg_read_file('/etc/passwd')",
    ),
    AttackCase(
        case_id="resource-stall",
        guarantee="availability of the retrieval plane",
        found="PF-FBP-8 denylist",
        sql="SELECT pg_sleep(30)",
    ),
    AttackCase(
        case_id="multi-statement",
        guarantee="single-statement execution",
        found="PF-FBP-8 denylist",
        sql="SELECT 1; SELECT 2",
    ),
    AttackCase(
        case_id="write-attempt",
        guarantee="read-only plane",
        found="PF-FBP-8 denylist",
        sql="UPDATE email_message SET subject = 'x'",
    ),
    AttackCase(
        case_id="select-into",
        guarantee="read-only plane",
        found="PF-FBP-8 denylist",
        sql="SELECT * INTO exfil FROM email_message",
    ),
    AttackCase(
        case_id="unterminated-literal",
        guarantee="fail-closed lexing (masking to end-of-input hid everything after a quote)",
        found="fix-wave review 2026-07-25",
        sql="SELECT id FROM email_message WHERE subject ILIKE '%unterminated",
    ),
)


# Assembled so every consumer still sees ONE list of everything that must be refused.
SQL_HATCH_ATTACKS: tuple[AttackCase, ...] = _PLANE_ATTACKS + FABRICATION_ATTACKS

__all__ = [
    "AllowedCase",
    "AttackCase",
    "RedactedCase",
    "LAUNDERING_INPUTS",
    "LAUNDERING_UUID",
    "REDACTED_STATEMENTS",
    "SQL_HATCH_ALLOWED",
    "SQL_HATCH_ATTACKS",
]


# — Statements that must KEEP working (hardening must not cost the product) ——————————

SQL_HATCH_ALLOWED: tuple[AllowedCase, ...] = (
    AllowedCase(
        case_id="plain-count",
        why_it_matters="the single most common shape the hatch exists to answer",
        sql="SELECT count(*) AS n FROM email_message",
    ),
    AllowedCase(
        case_id="exists-subquery-selects-one",
        why_it_matters="`EXISTS (SELECT 1 …)` is the ordinary way to ask whether a related row "
        "exists, and the planner renders the inner output as the bare constant `1`. The "
        "round-5 constant rule therefore refuses constants of ANY type only at the TOP level, "
        "and text constants at any depth — this case is what keeps that distinction honest.",
        sql="SELECT count(*) AS n FROM email_message m "
        "WHERE EXISTS (SELECT 1 FROM email_recipient r WHERE r.email_id = m.id)",
    ),
    AllowedCase(
        case_id="exists-in-the-target-list",
        why_it_matters="`EXISTS (…)` as a SELECTED column renders as `(hashed SubPlan 2)`. The "
        "provenance test must read a subplan reference as a corpus read — the subplan is "
        "scanned on its own account — or every has-this/does-that column is refused.",
        sql="SELECT m.id, EXISTS (SELECT 1 FROM email_attachment a WHERE a.email_id = m.id) "
        "AS has_attachment FROM email_message m",
    ),
    AllowedCase(
        case_id="scalar-subquery-column",
        why_it_matters="a scalar subquery is referenced at the top as the planner's `$0`, which "
        "reads nothing by itself. Judging it caller-authored sentinelled a real value "
        "(`(SELECT max(sent_at) FROM email_message)` came back redacted); the InitPlan that "
        "defines it is scanned separately, which is where the real answer is.",
        sql="SELECT (SELECT max(sent_at) FROM email_message) AS newest, count(*) AS n "
        "FROM email_message",
    ),
    AllowedCase(
        case_id="case-expression-label",
        why_it_matters="bucketing rows under a label is a normal analytical answer. The labels "
        "are string literals, but the OUTPUT entry is a CASE expression, not a constant — the "
        "round-5 rule must tell 'computed per row' from 'typed by the caller'.",
        sql="SELECT CASE WHEN direction = 'inbound' THEN 'in' ELSE 'out' END AS kind, "
        "count(*) AS n FROM email_message GROUP BY 1",
    ),
    AllowedCase(
        case_id="join-across-documented-tables",
        why_it_matters="attachments questions need the carrying message",
        sql="SELECT m.id, a.filename FROM email_message m "
        "JOIN email_attachment a ON a.email_id = m.id LIMIT 5",
    ),
    AllowedCase(
        case_id="literal-that-looks-like-a-forbidden-call",
        why_it_matters="a scan that reads search TERMS as code broke ~19 English words once; "
        "the words a user searches for are data and must stay data",
        sql="SELECT count(*) AS n FROM email_message WHERE subject ILIKE '%set_config(%'",
    ),
    AllowedCase(
        case_id="literal-with-denylisted-words",
        why_it_matters="'update' and 'security' are ordinary business-email vocabulary",
        sql="SELECT count(*) AS n FROM email_message WHERE body_text ILIKE '%update%' "
        "AND subject ILIKE '%security%'",
    ),
    AllowedCase(
        case_id="literal-with-desc-and-comment-markers",
        why_it_matters="rewriting or truncating inside a literal silently changes the question",
        sql="SELECT count(*) AS n FROM email_message WHERE subject ILIKE '%desc--x%'",
    ),
    AllowedCase(
        case_id="derived-counterparty-view",
        why_it_matters="the view is the documented aggregate surface; it must survive view "
        "expansion in the relation check",
        sql="SELECT count(*) AS n FROM counterparty_summary",
    ),
    AllowedCase(
        case_id="ranking-with-order-by-desc",
        why_it_matters="top-N is the hatch's other main job, and DESC gets rewritten in flight",
        sql="SELECT from_address, count(*) AS n FROM email_message "
        "GROUP BY from_address ORDER BY n DESC LIMIT 5",
    ),
    AllowedCase(
        case_id="people-lookup",
        why_it_matters="person/person_email are documented and must stay reachable",
        sql="SELECT p.display_name, e.email FROM person p "
        "JOIN person_email e ON e.person_id = p.id LIMIT 5",
    ),
    AllowedCase(
        case_id="text-and-numeric-functions",
        why_it_matters="an allowlist of functions is only safe if it actually covers the "
        "analytics real questions need — otherwise hardening quietly removes the feature",
        sql="SELECT lower(from_address) AS a, length(subject) AS l, "
        "coalesce(subject, '(none)') AS s FROM email_message LIMIT 5",
    ),
    AllowedCase(
        case_id="time-bucketed-aggregation",
        why_it_matters="'how did volume change over time' is a whole intent class",
        sql="SELECT date_trunc('month', sent_at) AS m, count(*) AS n "
        "FROM email_message GROUP BY 1 ORDER BY 1",
    ),
    AllowedCase(
        case_id="filter-by-a-known-id",
        why_it_matters="a uuid in a FILTER only matches rows that exist, so it must stay "
        "allowed — only a uuid SELECTED as an output column is fabricated evidence",
        sql="SELECT id, subject FROM email_message "
        "WHERE id = '44444444-4444-4444-4444-444444444444'::uuid",
    ),
    AllowedCase(
        case_id="date-window",
        why_it_matters="temporal questions are a whole intent class",
        sql="SELECT count(*) AS n FROM email_message "
        "WHERE sent_at >= DATE '2020-01-01' AND sent_at < DATE '2021-01-01'",
    ),
)
