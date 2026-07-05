"""counterparty_summary v2 — dossier view gains citable evidence ids (ask-tools loop MUT11b).

Role: Adds first_message_id / last_message_id (the earliest/latest email carrying the
      counterparty domain) so every dossier fact is citable — the citation-hardened reader
      prompt demands [id: <uuid>] per fact, and a payload without ids forces fabrication
      (measured: correct answers failed the invented-citation gate under 0020's view).
Used by: app.ask.tools get_counterparty_summary (reader plane).
Key invariants: WITH (security_invoker = true) — caller's RLS/visibility applies (never a
      plain owner-privilege view); read-only derived layer.

Revision ID: 0021_counterparty_summary_v2
Revises: 0020_counterparty_summary
"""

from alembic import op

revision = "0021_counterparty_summary_v2"
down_revision = "0020_counterparty_summary"
branch_labels = None
depends_on = None

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

_VIEW_V1_SQL = """
CREATE VIEW counterparty_summary WITH (security_invoker = true) AS
SELECT org_id,
       domain,
       min(sent_at)  AS first_contact,
       max(sent_at)  AS last_contact,
       count(*) FILTER (WHERE direction = 'inbound')  AS inbound_count,
       count(*) FILTER (WHERE direction = 'outbound') AS outbound_count,
       count(*)      AS total_mentions,
       count(DISTINCT address) AS distinct_addresses
FROM (
    SELECT m.org_id, lower(split_part(m.from_address, '@', 2)) AS domain,
           m.sent_at, m.direction, lower(m.from_address) AS address
    FROM email_message m
    WHERE m.from_address IS NOT NULL AND position('@' IN m.from_address) > 0
    UNION ALL
    SELECT r.org_id, lower(split_part(r.address, '@', 2)),
           m.sent_at, m.direction, lower(r.address)
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
    """Replace the view with the evidence-id-bearing v2 (grants re-applied after DROP)."""
    op.execute("DROP VIEW IF EXISTS counterparty_summary")
    op.execute(_VIEW_V2_SQL)
    for grant in _GRANTS:
        op.execute(grant)


def downgrade() -> None:
    """Restore the v1 view shape."""
    op.execute("DROP VIEW IF EXISTS counterparty_summary")
    op.execute(_VIEW_V1_SQL)
    for grant in _GRANTS:
        op.execute(grant)
