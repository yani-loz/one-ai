"""counterparty_summary v3 — volume columns count DISTINCT MESSAGES, not contact rows (F9/M-36b).

Role: Fixes a counting defect in the v2 rollup. The view derives its rows from a UNION ALL of
      sender-rows (one per email_message) and recipient-rows (one per email_recipient), so a
      single message contributes N+1 contact rows for a domain that appears N times on its
      to/cc line. v2's `count(*)`-based inbound_count / outbound_count / total_mentions therefore
      counted message×party CONTACT ROWS, not messages: one inbound email from a@acme.com CC'ing
      three acme.com colleagues counted acme.com four times. The lab (EXP-001, finding F9 /
      mutation M-36b) measured ~13× inflation on multi-recipient domains, which corrupted every
      ranking/"who do we email most" answer. The fix: the three volume columns become
      `count(DISTINCT message_id)`. `distinct_addresses` was already `count(DISTINCT address)`
      and is unchanged; `first/last_contact` and `first/last_message_id` are row-order facts
      unaffected by the defect and are kept EXACTLY as v2 (the citable ids are load-bearing).

      SEMANTIC CHANGE (deliberate): the column NAME `total_mentions` is preserved so every
      consumer (the sql_tool _M_SCHEMA card) keeps its shape, but its MEANING changes from
      "count of contact-row mentions" to "count of DISTINCT
      messages this domain participates in (as sender or recipient)". inbound_count/outbound_count
      likewise become distinct-message counts split by the message's direction (direction is a
      per-message attribute — every contact row of a message carries the same direction, so the
      FILTER is exact).

Used by: the generated-SQL hatch ONLY — sql_tool's _M_SCHEMA describes the view to the SQL
      specialist, and app.ask.tools.sql_execution allows it in the hatch's relation allowlist.
      There is no dedicated tool: get_counterparty_summary was rescinded (commit e4a4535), so
      the query shape reaching this view is whatever the specialist generates, not a reviewed
      one — which is exactly why security_invoker below is load-bearing.
Key invariants: WITH (security_invoker = true) — the caller's RLS/visibility applies (never a
      plain owner-privilege view); read-only derived layer. Grants re-applied after the recreate.

Revision ID: 0022_counterparty_summary_v3
Revises: 0021_counterparty_summary_v2
"""

from alembic import op

revision = "0022_counterparty_summary_v3"
down_revision = "0021_counterparty_summary_v2"
branch_labels = None
depends_on = None

# v3: inbound/outbound/total are count(DISTINCT message_id); everything else identical to v2.
_VIEW_V3_SQL = """
CREATE VIEW counterparty_summary WITH (security_invoker = true) AS
SELECT org_id,
       domain,
       min(sent_at)  AS first_contact,
       max(sent_at)  AS last_contact,
       (array_agg(message_id ORDER BY sent_at ASC))[1]  AS first_message_id,
       (array_agg(message_id ORDER BY sent_at DESC))[1] AS last_message_id,
       count(DISTINCT message_id) FILTER (WHERE direction = 'inbound')  AS inbound_count,
       count(DISTINCT message_id) FILTER (WHERE direction = 'outbound') AS outbound_count,
       count(DISTINCT message_id) AS total_mentions,
       count(DISTINCT address) AS distinct_addresses
FROM (
    SELECT m.org_id, lower(split_part(m.from_address, '@', 2)) AS domain,
           m.sent_at, m.direction, lower(m.from_address) AS address, m.id AS message_id
    FROM email_message m
    WHERE m.from_address IS NOT NULL AND position('@' IN m.from_address) > 0
    UNION ALL
    SELECT r.org_id, lower(split_part(r.address, '@', 2)),
           m.sent_at, m.direction, lower(r.address), m.id
    FROM email_recipient r
    JOIN email_message m ON m.id = r.email_id AND m.org_id = r.org_id
    WHERE position('@' IN r.address) > 0
) contacts
WHERE domain <> ''
GROUP BY org_id, domain
"""

# The exact v2 SQL (contact-row counts) — restored verbatim on downgrade (copied from 0021).
_VIEW_V2_SQL = """
CREATE VIEW counterparty_summary WITH (security_invoker = true) AS
SELECT org_id,
       domain,
       min(sent_at)  AS first_contact,
       max(sent_at)  AS last_contact,
       (array_agg(message_id ORDER BY sent_at ASC))[1]  AS first_message_id,
       (array_agg(message_id ORDER BY sent_at DESC))[1] AS last_message_id,
       count(*) FILTER (WHERE direction = 'inbound')  AS inbound_count,
       count(*) FILTER (WHERE direction = 'outbound') AS outbound_count,
       count(*)      AS total_mentions,
       count(DISTINCT address) AS distinct_addresses
FROM (
    SELECT m.org_id, lower(split_part(m.from_address, '@', 2)) AS domain,
           m.sent_at, m.direction, lower(m.from_address) AS address, m.id AS message_id
    FROM email_message m
    WHERE m.from_address IS NOT NULL AND position('@' IN m.from_address) > 0
    UNION ALL
    SELECT r.org_id, lower(split_part(r.address, '@', 2)),
           m.sent_at, m.direction, lower(r.address), m.id
    FROM email_recipient r
    JOIN email_message m ON m.id = r.email_id AND m.org_id = r.org_id
    WHERE position('@' IN r.address) > 0
) contacts
WHERE domain <> ''
GROUP BY org_id, domain
"""

_GRANTS = (
    "GRANT SELECT ON counterparty_summary TO oneai_reader",
    "GRANT SELECT ON counterparty_summary TO oneai_app",
)


def upgrade() -> None:
    """Replace the view with v3 (DISTINCT-message volumes); grants re-applied after DROP."""
    op.execute("DROP VIEW IF EXISTS counterparty_summary")
    op.execute(_VIEW_V3_SQL)
    for grant in _GRANTS:
        op.execute(grant)


def downgrade() -> None:
    """Restore the exact v2 view shape (contact-row counts)."""
    op.execute("DROP VIEW IF EXISTS counterparty_summary")
    op.execute(_VIEW_V2_SQL)
    for grant in _GRANTS:
        op.execute(grant)
