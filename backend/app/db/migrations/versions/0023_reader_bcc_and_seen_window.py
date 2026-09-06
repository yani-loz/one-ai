"""Put the BCC rule and the seen-window rule in the DATABASE, not in hand-written tool SQL.

Role: Closes a per-person visibility break the R5 isolation red team found: the BCC rule and the
      write-plane seen-window rule were enforced ONLY in four hand-written tool queries, while
      three other planes read the same tables and had neither.

      The `visibility` policy from 0019 keys CHILD rows on the PARENT (`email_recipient.email_id`),
      so any grant holder on a message can read EVERY recipient row of it — BCC included. That is
      deliberate at the policy level and was compensated for in Python. The compensation covered
      `email_read`, `email_filters` (x2) and `person_tool`. It did NOT cover:
        * the generated-SQL hatch, which allows `email_recipient`, `person` and `acl_grant`
          outright — an ordinary question ("who else was on that email?") produces
          `SELECT * FROM email_recipient WHERE email_id = …` and returns BCC rows verbatim;
        * `counterparty_summary`, whose recipient arm has no `kind` filter, making it a precise
          membership ORACLE — its BCC-inclusive `total_mentions` differs from `count_emails`'s
          BCC-exclusive count by exactly the number of readable messages where a domain appears
          only as BCC;
        * `acl_grant`, which carries one row per recipient of EVERY kind, so joining it to
          `person`/`person_email` re-discloses the blind-copied parties BY NAME without ever
          touching `email_recipient` — and, with only org isolation on it, also enumerates
          message ids and mailbox sizes belonging to colleagues.

      Ledger V1-V8 closed the four queries; S9 closed the hatch's table REACH but settled on an
      allowlist that includes exactly the tables where those rules live. The two hardening efforts
      never met. This migration puts the rules where the planes converge.

Used by: every reader-plane query. The four tool filters stay as defence in depth and keep their
      own pins — per the ledger, an outcome pin is not a causal pin.
Depends on: 0019_permission_fidelity (the reader role, `visibility`, `org_isolation`),
      0022_counterparty_summary_v3 (the view that inherits this through security_invoker),
      email_recipient / person / acl_grant.
Key invariants:
  - **THE RULE FOR EVERY FUTURE CHILD TABLE.** `_CONTENT_TABLES` in 0019 keys children on the
    PARENT (`email_recipient.email_id`, `email_attachment.email_id`), so a grant on a message
    admits EVERY child row of it. Any per-child rule — "this kind of row is not disclosed" —
    therefore has NO home in that policy and must be its own RESTRICTIVE policy here. Enforcing
    it in application SQL instead is what produced this migration: the rule was correct in four
    tool queries and absent from the three other planes that read the same tables. When a new
    child table is added under `_CONTENT_TABLES`, ask what it discloses that the parent's grant
    does not cover, and put the answer in the DATABASE. (`email_attachment` needs nothing today:
    it has no recipient-derived column — attachments belong to the message, not to a party.)
  - RESTRICTIVE policies AND with everything else, so these can only ever narrow what is visible.
  - `recipient_kind` is a NO-OP for the four tool queries (they already exclude bcc) and for the
    write plane (`oneai_app` is not the reader role, and dedup/extraction must still see
    every row).
  - `own_grants` is a no-op for the `visibility` policy itself: that policy's EXISTS already
    constrains `g.person_id = current_setting('app.current_person_id')`, which is precisely what
    this policy allows. It does NOT recurse — the policy body reads no table.
  - The seen-window columns are revoked at COLUMN level, so the M-Schema card stops being the
    thing that keeps them out of reach. A schema card is documentation, never a boundary.

Revision ID: 0023_reader_bcc_and_seen_window
Revises: 0022_counterparty_summary_v3
"""

from alembic import op

revision = "0023_reader_bcc_and_seen_window"
down_revision = "0022_counterparty_summary_v3"
branch_labels = None
depends_on = None

_READER_ROLE = "oneai_reader"
_PERSON_GUC = "NULLIF(current_setting('app.current_person_id', true), '')::uuid"


def upgrade() -> None:
    # ── 1) The BCC rule, in the database ────────────────────────────────────────────────────
    # `kind IN ('to','cc')` rather than `kind <> 'bcc'`: the column is a five-value enum
    # ('to','cc','bcc','reply_to','sender') and a denylist over an enum admits every value
    # nobody thought about. `reply_to`/`sender` are header artefacts, not disclosed recipients,
    # and `get_email`'s own description already promises "to/cc" only.
    op.execute(
        f"""
        CREATE POLICY recipient_kind ON email_recipient
            AS RESTRICTIVE
            FOR SELECT
            TO {_READER_ROLE}
            USING (kind IN ('to', 'cc'))
        """
    )

    # ── 2) A reader may see only their OWN grants ───────────────────────────────────────────
    # acl_grant was allowed on the retrieval plane as "grant bookkeeping, never content". It is
    # content by another route: one row per recipient of every kind, joinable to person and
    # person_email, which reconstructs the blind-copied recipient list by name. Org isolation
    # alone also let a caller enumerate object ids they hold no grant for, which colleagues are
    # party to each, and (by count) each colleague's mailbox size.
    op.execute(
        f"""
        CREATE POLICY own_grants ON acl_grant
            AS RESTRICTIVE
            FOR SELECT
            TO {_READER_ROLE}
            USING (person_id = {_PERSON_GUC})
        """
    )

    # ── 3) The write-plane seen window leaves the reader plane ──────────────────────────────
    # `person.first_seen_at`/`last_seen_at` are maintained over EVERY ingested message,
    # including ones the caller holds no grant for, and including BCC-only contacts (the
    # ingest service's _NON_PERSON_RECIPIENT_KINDS excludes reply_to/sender but NOT bcc, so a
    # blind-copied contact gets a person row and a moving window). `find_person` recomputes the
    # window from READABLE messages for exactly this reason (ledger V3/V4) — and the hatch
    # served the raw columns straight past that fix.
    #
    # A column-level REVOKE does NOTHING while a TABLE-level grant stands: `GRANT SELECT ON
    # person` implies every column, and revoking two of them leaves the table grant intact
    # (measured — the revoke applied cleanly and the column stayed readable). The table grant
    # has to go first, and the columns that remain readable are then granted explicitly.
    # `find_person` selects id / org_id / display_name / is_internal only, so this is a no-op
    # for it; any future reader query needing a new column must ask for it here, which is the
    # point.
    op.execute(f"REVOKE SELECT ON TABLE person FROM {_READER_ROLE}")
    op.execute(
        "GRANT SELECT (id, org_id, display_name, is_internal, created_at, updated_at) "
        f"ON TABLE person TO {_READER_ROLE}"
    )


def downgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE person TO {_READER_ROLE}")
    op.execute("DROP POLICY IF EXISTS own_grants ON acl_grant")
    op.execute("DROP POLICY IF EXISTS recipient_kind ON email_recipient")
