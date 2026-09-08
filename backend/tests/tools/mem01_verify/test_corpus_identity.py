"""
Role: Seals CORPUS_DIGEST_V1 (contract §5.2, §1.4) on the probe — the identity fields, per-table
      roster counts, agreement of `text_digest` with the snapshot emitter, determinism, and the
      sensitivity rule: a non-text gate-relevant column change moves `corpus_digest` but not
      `text_digest`; a new row moves both.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.corpus_identity, .snapshot, .db (imported inside each test); the
      session probe corpus (the iso org is mutated here through the probe's global plane).
Key invariants:
  - Sensitivity is asserted relatively (before vs after inside one test), never on absolute
    counts of the iso org, which other tests also grow.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from tests.tools.mem01_verify import seeding
from tests.tools.mem01_verify.conftest import (
    SESSION_LOOP,
    DevServer,
    InstrumentLoader,
    ProbeCorpusFactory,
)

HEX64 = re.compile(r"[0-9a-f]{64}")


async def _identity(instrument: InstrumentLoader, corpus: object, org_id: object) -> object:
    db = instrument("db")
    corpus_identity = instrument("corpus_identity")
    async with db.readonly_corpus_snapshot(org_id, database=corpus.database) as conn:  # type: ignore[attr-defined]
        return await corpus_identity.corpus_digest(conn, org_id)


def test_version_constant(instrument: InstrumentLoader) -> None:
    assert instrument("corpus_identity").CORPUS_DIGEST_VERSION == "CORPUS_DIGEST_V1"


@SESSION_LOOP
async def test_corpus_identity_fields_and_roster_counts_of_the_small_org(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, dev_server: DevServer
) -> None:
    corpus = await probe_corpus()
    small = corpus.small

    identity = await _identity(instrument, corpus, small.org_id)

    assert identity.version == "CORPUS_DIGEST_V1"
    assert HEX64.fullmatch(identity.corpus_digest) and HEX64.fullmatch(identity.text_digest)
    assert identity.corpus_digest != identity.text_digest
    counts = dict(identity.roster_counts)
    assert counts["email_message"] == small.email_count
    assert counts["email_attachment"] == small.attachment_count
    assert counts["email_recipient"] == 6 and counts["acl_grant"] == small.grant_count
    assert counts["person"] == 3 and counts["person_email"] == 3
    assert identity.database == corpus.database and identity.org_id == small.org_id
    assert identity.host == dev_server.host and identity.port == dev_server.port
    assert isinstance(identity.taken_at, datetime) and identity.taken_at.tzinfo is not None
    assert isinstance(identity.snapshot_transaction_id, str) and identity.snapshot_transaction_id


@SESSION_LOOP
async def test_text_digest_agrees_with_the_snapshot_emitter(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory, tmp_path: Path
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    snapshot = instrument("snapshot")
    small = corpus.small

    identity = await _identity(instrument, corpus, small.org_id)
    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        summary = await snapshot.emit_snapshot(conn, small.org_id, tmp_path)

    assert identity.text_digest == summary.text_digest


@SESSION_LOOP
async def test_corpus_digest_is_deterministic_and_tenant_specific(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()

    first = await _identity(instrument, corpus, corpus.small.org_id)
    second = await _identity(instrument, corpus, corpus.small.org_id)
    other = await _identity(instrument, corpus, corpus.big.org_id)

    assert (first.corpus_digest, first.text_digest) == (second.corpus_digest, second.text_digest)
    assert other.corpus_digest != first.corpus_digest
    assert other.roster_counts["email_message"] == corpus.big.email_count


@SESSION_LOOP
async def test_non_text_column_change_moves_corpus_digest_only_and_a_new_row_moves_both(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    iso = corpus.iso
    sessions = db.probe_session_factories(corpus.database)
    baseline = await _identity(instrument, corpus, iso.org_id)

    async with sessions.global_() as writer:
        await seeding.assert_probe_connection(writer)
        await writer.execute(
            text("UPDATE email_message SET language = 'bg' WHERE id = :id"),
            {"id": str(iso.email_ids[0])},
        )
        await writer.commit()
    after_column = await _identity(instrument, corpus, iso.org_id)
    async with sessions.global_() as writer:
        await seeding.add_iso_email(writer, iso, 200)
        await writer.commit()
    after_row = await _identity(instrument, corpus, iso.org_id)

    assert after_column.corpus_digest != baseline.corpus_digest
    assert after_column.text_digest == baseline.text_digest
    assert after_row.corpus_digest != after_column.corpus_digest
    assert after_row.text_digest != after_column.text_digest
    assert (
        after_row.roster_counts["email_message"] == after_column.roster_counts["email_message"] + 1
    )


@SESSION_LOOP
async def test_metadata_changes_and_nullness_move_corpus_digest_but_never_text_digest(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    iso = corpus.iso
    sessions = instrument("db").probe_session_factories(corpus.database)
    email_id, attachment_id = str(iso.email_ids[1]), str(iso.attachment_ids[0])
    mutations = [
        (
            "UPDATE email_message SET headers = CAST(:h AS jsonb) WHERE id = :id",
            {"h": '{"Content-Language": "bg"}', "id": email_id},
        ),
        (
            "UPDATE acl_grant SET revoked_at = now() WHERE org_id = :o AND object_id = :id",
            {"o": str(iso.org_id), "id": str(iso.email_ids[0])},
        ),
        (
            "UPDATE email_attachment SET extraction_status = 'corrupt' WHERE id = :id",
            {"id": attachment_id},
        ),
        ("UPDATE email_message SET received_at = NULL WHERE id = :id", {"id": email_id}),
    ]
    identities = [await _identity(instrument, corpus, iso.org_id)]

    for statement, params in mutations:
        async with sessions.global_() as writer:
            await seeding.assert_probe_connection(writer)
            result = await writer.execute(text(statement), params)
            assert result.rowcount == 1, statement
            await writer.commit()
        identities.append(await _identity(instrument, corpus, iso.org_id))

    corpus_digests = [identity.corpus_digest for identity in identities]
    assert len(set(corpus_digests)) == len(corpus_digests)  # every change moved the digest
    assert len({identity.text_digest for identity in identities}) == 1
