"""IDEM scenarios whose primary criterion is `idem.replay_no_change`.

Role:
    Eight conformance scenarios that pin the replay rule: the first ingest creates (the
    positive control), and every later presentation of the same input — a plain replay, a
    replay retried after an injected failure, a replay under a different delivery
    envelope, a replay that reuses a stored extraction inline, three replays in a row —
    changes no row in any of the five tracked tables. Data only.
Used by:
    `tools.mem01_verify.fixtures.idem_scenarios_d` (which assembles `IDEM_SCENARIOS`)
    and through it `tools.mem01_verify.fixtures.idem_scenarios`.
Depends on:
    `tools.mem01_verify.fixtures.idem_scenarios_a` (record types) and `…_b` (the
    synthetic originals and the delta constructors). Nothing else inside the project.
Key invariants:
    - Contract R12: every expectation is transcribed from criterion 5 (`IDEM`), from
      ruling (c), or from the synthetic ORIGINAL — never from a measured component.
    - Each scenario's step `s1` creates (email_message +1, recipients from the ORIGINAL,
      acl_grant floored at 1); every later step expects an exact delta of ZERO on all five
      tracked tables. Only email_message carries an EXACT value on a creation step — that
      number comes from the criterion; the rest are floors read off the ORIGINAL.
    - Each scenario also joins the denominator of `idem.exactly_once_committed` through
      `also_pins`, and declares the canonical keys that must end with exactly one
      committed logical result.
"""

from __future__ import annotations

from typing import Final

from tools.mem01_verify.fixtures.idem_scenarios_a import (
    IdemScenario,
    ScenarioExpectation,
    ScenarioStep,
)
from tools.mem01_verify.fixtures.idem_scenarios_b import (
    ANNA,
    BORIS,
    CLARA,
    DIMITAR,
    ELENA,
    EXACTLY_ONCE_CRITERION,
    M001,
    M002,
    M003,
    M006,
    M007,
    M008,
    M009,
    M010,
    REPLAY_CRITERION,
    RULING_C_ORIGIN,
    creation_deltas,
    ingest_step,
    replay_step,
    zero_deltas,
)

SCENARIO_001: Final = IdemScenario(
    case_id="idem-001",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Pins the positive control plus the base replay rule: a single-part bilingual message "
        "creates on first ingest and its byte-identical replay changes nothing."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates exactly 1 email_message and at least 1 email_recipient; s2 changes no "
            "row in any of the five tracked tables."
        ),
        canonical_keys=("idem-001-a@acme.test",),
    ),
    messages=(M001,),
    steps=(
        ingest_step(M001),
        replay_step(M001, "s2", "Byte-identical replay under the same versions/configuration."),
    ),
    prebound_identities=(ANNA, BORIS),
)

SCENARIO_002: Final = IdemScenario(
    case_id="idem-002",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Pins that carrier rows obey the replay rule too: ruling (c) names carrier rows "
        "alongside content and grant rows."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates exactly 1 message, at least 2 recipients and at least 2 attachment "
            "carriers; s2 adds nothing, including no second carrier for either attachment."
        ),
        canonical_keys=("idem-002-a@acme.test",),
    ),
    messages=(M002,),
    steps=(
        ingest_step(M002),
        replay_step(M002, "s2", "Replay of a two-attachment message; carriers must not double."),
    ),
    prebound_identities=(ANNA, BORIS, CLARA),
)

SCENARIO_003: Final = IdemScenario(
    case_id="idem-003",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Contract 10.6: 'retry after a simulated failure adds zero'. A failure injected inside "
        "a replay, then retried, must still leave durable state untouched."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates; s2 fails mid-replay and is retried to completion, and the net delta "
            "across the failed attempt plus the retry is zero on every tracked table."
        ),
        canonical_keys=("idem-003-a@acme.test",),
    ),
    messages=(M003,),
    steps=(
        ingest_step(M003),
        ScenarioStep(
            step_id="s2",
            action="retry_after_failure",
            payload_ref="m1",
            description=(
                "Replay the same bytes; abort the attempt after the message row is examined but "
                "before the transaction commits, then retry the whole replay to completion."
            ),
            deltas=zero_deltas(
                "contract 10.6 + ruling (c): a retried replay adds zero, and the aborted "
                "attempt leaves no partial rows behind"
            ),
            failure_injection="abort_before_commit_of_replay_transaction",
        ),
    ),
    prebound_identities=(BORIS, DIMITAR),
)

SCENARIO_006: Final = IdemScenario(
    case_id="idem-006",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Cyrillic subject, Cyrillic body and an RFC 2231 Cyrillic filename must not change the "
        "canonical input identity between two runs (a re-encoding round trip is not a change)."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates exactly 1 message, at least 1 recipient and at least 1 carrier; "
            "s2 adds nothing."
        ),
        canonical_keys=("idem-006-a@acme.test",),
    ),
    messages=(M006,),
    steps=(
        ingest_step(M006),
        replay_step(M006, "s2", "Replay of the Bulgarian message with a Cyrillic filename."),
    ),
    prebound_identities=(BORIS, ELENA),
)

SCENARIO_007: Final = IdemScenario(
    case_id="idem-007",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Bcc recipients are recipient rows too: a replay must not add a second Bcc row, and "
        "the grant plane must not gain a row either (ruling (c) names grant rows)."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates exactly 1 message and at least 3 recipient rows (the To, Cc and Bcc "
            "addresses of the ORIGINAL); s2 adds zero rows to every tracked table, "
            "acl_grant included."
        ),
        canonical_keys=("idem-007-a@acme.test",),
    ),
    messages=(M007,),
    steps=(
        ingest_step(M007),
        replay_step(M007, "s2", "Replay of a message carrying a blind-copied recipient."),
    ),
    prebound_identities=(ANNA, BORIS, CLARA, ELENA),
)

SCENARIO_008: Final = IdemScenario(
    case_id="idem-008",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "The canonical input is the message, not the delivery envelope: re-fetching the same "
        "bytes under a different mailbox folder and IMAP UID is a replay, not a new input."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates from folder INBOX/uid 101; s2 replays identical bytes presented as "
            "folder Archive/uid 902 and adds zero rows."
        ),
        canonical_keys=("idem-008-a@acme.test",),
    ),
    messages=(M008,),
    steps=(
        ScenarioStep(
            step_id="s1",
            action="ingest",
            payload_ref="m1",
            description="First ingest of m1 from INBOX; must create exactly one logical result.",
            deltas=creation_deltas(M008),
            envelope=(("mailbox_folder", "INBOX"), ("imap_uid", "101")),
        ),
        ScenarioStep(
            step_id="s2",
            action="replay",
            payload_ref="m1",
            description="Identical bytes re-presented under a different folder and IMAP UID.",
            deltas=zero_deltas(RULING_C_ORIGIN),
            envelope=(("mailbox_folder", "Archive/2026"), ("imap_uid", "902")),
        ),
    ),
    prebound_identities=(BORIS, DIMITAR, ELENA),
    notes=(
        "The envelope difference is arranged by the harness from each step's `envelope` "
        "pairs; the .eml bytes fed to both steps are byte-identical."
    ),
)

SCENARIO_009: Final = IdemScenario(
    case_id="idem-009",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Ruling (c): inline reuse may keep the old result ONLY with its provenance and version "
        "unchanged; a replay may neither add a row nor restamp the existing one."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates 1 message and 1 carrier; s2 adds zero rows AND leaves the carrier's "
            "extractor name, extractor version and extraction status byte-identical."
        ),
        canonical_keys=("idem-009-a@acme.test",),
    ),
    messages=(M009,),
    steps=(
        ingest_step(M009),
        ScenarioStep(
            step_id="s2",
            action="replay",
            payload_ref="m1",
            description="Replay under unchanged versions; the stored result is reused inline.",
            deltas=zero_deltas(RULING_C_ORIGIN),
            must_remain_unchanged=(
                "email_attachment.extractor_name",
                "email_attachment.extractor_version",
                "email_attachment.extraction_status",
                "email_attachment.content_hash",
                "email_message.parse_status",
            ),
        ),
    ),
    prebound_identities=(ANNA, BORIS),
)

SCENARIO_010: Final = IdemScenario(
    case_id="idem-010",
    criterion_id=REPLAY_CRITERION,
    also_pins=(EXACTLY_ONCE_CRITERION,),
    origin=(
        "Idempotence is not a one-shot property: the second, third and fourth presentations of "
        "the same input must each change nothing."
    ),
    expected=ScenarioExpectation(
        summary="s1 creates; s2, s3 and s4 each add zero rows to every tracked table.",
        canonical_keys=("idem-010-a@acme.test",),
    ),
    messages=(M010,),
    steps=(
        ingest_step(M010),
        replay_step(M010, "s2", "First replay."),
        replay_step(M010, "s3", "Second replay."),
        replay_step(M010, "s4", "Third replay."),
    ),
    prebound_identities=(ANNA, BORIS, DIMITAR),
)
