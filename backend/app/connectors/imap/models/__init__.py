"""
Role: IMAP email model package — imports the email Layer-1 ORM models so they register on
      Base.metadata.
Used by: app.db.migrations.env (target metadata), the IMAP email repository, the standing
         RLS-invariant test, and the test schema fixtures.
Depends on: app.connectors.imap.models.email.
Key invariants:
  - Importing this package registers the email tables on Base.metadata — relied on by
    db.migrations.env, the RLS-invariant test (which would otherwise miss these tenant tables),
    and the test schema fixtures.
"""

from __future__ import annotations

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient

__all__ = ["EmailAttachment", "EmailMessage", "EmailRecipient"]
