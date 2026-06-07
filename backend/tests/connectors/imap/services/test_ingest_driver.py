"""
Role: Test the dev disk-ingest driver's per-message ERROR ISOLATION contract — "one bad email never
      aborts the run". Drives _ingest_mailbox over a valid + an unreadable path and asserts the run
      completes with an honest stored/failed tally.
Used by: pytest (tests/connectors/imap/services). Real DB via the services conftest (ingest_schema);
         _ingest_mailbox opens its OWN session, so this uses the schema fixture, not db_session.
Depends on: scripts.ingest_imap_dump (the driver), the services conftest schema fixture.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from scripts.ingest_imap_dump import _ingest_mailbox


async def test_ingest_mailbox_isolates_a_failing_email(ingest_schema: None, tmp_path: Path) -> None:
    valid = tmp_path / "ok.eml"
    valid.write_bytes(b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <ok@x>\r\n\r\nbody")
    missing = tmp_path / "missing.eml"  # never created → read_bytes raises → must be isolated

    tally = await _ingest_mailbox("owner@acme.com", [valid, missing], uuid4())

    assert tally["stored"] == 1  # the valid email still got through
    assert tally["failed"] == 1  # the unreadable one was counted, not allowed to abort the run
