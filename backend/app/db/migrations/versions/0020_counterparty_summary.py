"""counterparty_summary view — derived-layer dossier projection (ask-tools loop MUT11).

Role: One-hop relationship rollup per counterparty email domain: first/last contact,
      directional volumes, address counts — computed over email_message + email_recipient.
Used by: app.ask.tools (get_counterparty_summary) on the reader plane.
Key invariants:
  - WITH (security_invoker = true): the view executes with the CALLER's privileges, so the
    reader role's org RLS + PF-01 visibility policies on the underlying tables fully apply —
    a plain view would execute as the owner and silently bypass RLS (never do that).
  - Read-only derived layer: no raw-table semantics change; dropping the view loses nothing.

Revision ID: 0020_counterparty_summary
Revises: 0019_permission_fidelity
"""

from alembic import op

revision = "0020_counterparty_summary"
down_revision = "0019_permission_fidelity"
branch_labels = None
depends_on = None

_VIEW_SQL = """
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


def upgrade() -> None:
    """Create the security-invoker dossier view + runtime read grants."""
    op.execute(_VIEW_SQL)
    op.execute("GRANT SELECT ON counterparty_summary TO oneai_reader")
    op.execute("GRANT SELECT ON counterparty_summary TO oneai_app")


def downgrade() -> None:
    """Drop the derived view (no raw data affected)."""
    op.execute("DROP VIEW IF EXISTS counterparty_summary")
