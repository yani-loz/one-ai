"""IDEM exactly-once scenarios, the stage-C backfill shape, and the assembled battery.

Role:
    Four conformance scenarios that pin exactly-once semantics — a concurrent first
    ingest, concurrent replays of an already-committed message, a retry after a failure
    injected inside the first ingest, and a ten-recipient fan-out — plus `idem-013`, the
    stage-C record shape for `idem.backfill_one_new_version`. Assembles `IDEM_SCENARIOS`
    from this module and `…_c`. Data only.
Used by:
    `tools.mem01_verify.fixtures.idem_scenarios` (the public re-export), and through it
    the IDEM gate evaluator and `tools.mem01_verify.fixtures.digest`.
Depends on:
    `tools.mem01_verify.fixtures.idem_scenarios_a` (record types), `…_b` (originals and
    delta constructors) and `…_c` (the replay-primary scenarios).
Key invariants:
    - Contract R12: every expectation is transcribed from criterion 5 (`IDEM`), from
      ruling (c), or from the synthetic ORIGINAL — never from a measured component.
    - "Exactly once" means one committed logical RESULT per canonical key, not one
      attempt: concurrency and retries may never manufacture a second result, and may
      never leave a partial one behind.
    - `idem-013` carries `stage_available="C"`; a stage-A run reports it `incomplete`
      and never PASS (contract R3).
    - `IDEM_SCENARIOS` is ordered by `case_id` and holds thirteen scenarios, so both
      `idem.replay_no_change` and `idem.exactly_once_committed` clear their `minimum: 10`.
"""

from __future__ import annotations

from typing import Final

from tools.mem01_verify.fixtures.idem_scenarios_a import (
    IdemScenario,
    RowDelta,
    ScenarioExpectation,
    ScenarioStep,
)
from tools.mem01_verify.fixtures.idem_scenarios_b import (
    ANNA,
    BACKFILL_CRITERION,
    BORIS,
    CLARA,
    DIMITAR,
    ELENA,
    EXACTLY_ONCE_CRITERION,
    M004A,
    M004B,
    M005,
    M011A,
    M011B,
    M012,
    M013,
    REPLAY_CRITERION,
    creation_deltas,
    ingest_step,
    replay_step,
    zero_deltas,
)
from tools.mem01_verify.fixtures.idem_scenarios_c import (
    SCENARIO_001,
    SCENARIO_002,
    SCENARIO_003,
    SCENARIO_006,
    SCENARIO_007,
    SCENARIO_008,
    SCENARIO_009,
    SCENARIO_010,
)

SCENARIO_004: Final = IdemScenario(
    case_id="idem-004",
    criterion_id=EXACTLY_ONCE_CRITERION,
    also_pins=(REPLAY_CRITERION,),
    origin=(
        "Contract 10.6: 'concurrent duplicate ingest adds one'. Three simultaneous first "
        "ingests of one never-seen message must commit exactly one logical result."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s2 runs three concurrent ingests of m2 and commits exactly ONE message (never "
            "two) with at least its two recipient rows; s3 replays m2 and adds nothing."
        ),
        canonical_keys=("idem-004-a@acme.test", "idem-004-b@acme.test"),
    ),
    messages=(M004A, M004B),
    steps=(
        ingest_step(M004A),
        ScenarioStep(
            step_id="s2",
            action="concurrent_duplicate",
            payload_ref="m2",
            description="Three simultaneous first ingests of the same fresh message bytes.",
            deltas=creation_deltas(M004B),
            concurrency=3,
        ),
        replay_step(M004B, "s3", "Replay after the concurrent race settled; nothing is added."),
    ),
    prebound_identities=(ANNA, BORIS, CLARA),
)

SCENARIO_005: Final = IdemScenario(
    case_id="idem-005",
    criterion_id=EXACTLY_ONCE_CRITERION,
    also_pins=(REPLAY_CRITERION,),
    origin=(
        "Exactly-once is one committed logical RESULT, not one attempt: four concurrent "
        "replays of an already-committed message add nothing and never a second result."
    ),
    expected=ScenarioExpectation(
        summary="s1 creates; s2 runs four concurrent replays and every tracked table gains zero.",
        canonical_keys=("idem-005-a@acme.test",),
    ),
    messages=(M005,),
    steps=(
        ingest_step(M005),
        ScenarioStep(
            step_id="s2",
            action="concurrent_duplicate",
            payload_ref="m1",
            description="Four simultaneous replays of an already-ingested message.",
            deltas=zero_deltas(
                "criterion 5: exactly one committed logical result per key — concurrency may "
                "not manufacture a second one"
            ),
            concurrency=4,
        ),
    ),
    prebound_identities=(BORIS, CLARA),
)

SCENARIO_011: Final = IdemScenario(
    case_id="idem-011",
    criterion_id=EXACTLY_ONCE_CRITERION,
    also_pins=(REPLAY_CRITERION,),
    origin=(
        "A failure during the FIRST ingest must leave nothing behind: the retry then commits "
        "exactly one complete result, never a partial set plus a duplicate."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s2 aborts after the message row is written but before the recipient rows commit, "
            "then retries: the net delta is exactly ONE message with at least its 3 "
            "recipients and 1 carrier — no orphan message row and no duplicate; s3 replays "
            "and adds nothing."
        ),
        canonical_keys=("idem-011-a@acme.test", "idem-011-b@acme.test"),
    ),
    messages=(M011A, M011B),
    steps=(
        ingest_step(M011A),
        ScenarioStep(
            step_id="s2",
            action="retry_after_failure",
            payload_ref="m2",
            description=(
                "Abort the first ingest of m2 between the message insert and the recipient "
                "inserts, then retry the whole ingest."
            ),
            deltas=creation_deltas(M011B),
            failure_injection="abort_after_email_message_insert_before_email_recipient_insert",
        ),
        replay_step(M011B, "s3", "Replay after the retried creation; nothing is added."),
    ),
    prebound_identities=(ANNA, BORIS, CLARA, ELENA),
)

SCENARIO_012: Final = IdemScenario(
    case_id="idem-012",
    criterion_id=EXACTLY_ONCE_CRITERION,
    also_pins=(REPLAY_CRITERION,),
    origin=(
        "Fan-out stress: ten recipients across To/Cc/Bcc and three parts including an inline "
        "image; replay must not multiply recipient rows, carriers or grants."
    ),
    expected=ScenarioExpectation(
        summary=(
            "s1 creates exactly 1 message with at least 10 recipient rows and at least 2 "
            "carriers (the two non-inline parts; the inline image may be excluded by the "
            "frozen scope policy); s2 adds zero rows everywhere."
        ),
        canonical_keys=("idem-012-a@acme.test",),
    ),
    messages=(M012,),
    steps=(
        ingest_step(M012, attachments_at_least=2),
        replay_step(M012, "s2", "Replay of the fan-out message with an inline image part."),
    ),
    prebound_identities=(ANNA, BORIS, CLARA, DIMITAR, ELENA),
    notes=(
        "The email_attachment floor is 2, not 3: whether the inline image part earns a "
        "carrier row is a scope-policy question, and R12 forbids reading the answer off code."
    ),
)

SCENARIO_013: Final = IdemScenario(
    case_id="idem-013",
    criterion_id=BACKFILL_CRITERION,
    also_pins=(),
    origin=(
        "Ruling (c) backfill semantics: an explicitly declared version backfill publishes "
        "exactly one new result per affected canonical input, and never presents the old "
        "result as processed by the new version. Shape only — stage C."
    ),
    expected=ScenarioExpectation(
        summary=(
            "After s1 creates at extractor version v1, s2 declares a backfill to v2 and must "
            "publish exactly one new result for the affected canonical input; the v1 result "
            "keeps its own provenance and is never restamped. Stage C: a stage-A run reports "
            "this case incomplete and never PASS."
        ),
        canonical_keys=("idem-013-a@acme.test",),
    ),
    messages=(M013,),
    steps=(
        ingest_step(M013),
        ScenarioStep(
            step_id="s2",
            action="backfill",
            payload_ref="m1",
            description=(
                "Declare an explicit extractor version backfill (v1 -> v2) covering the stored "
                "carrier of m1."
            ),
            deltas=(
                RowDelta("email_message", 0, None, "backfill republishes results, not messages"),
                RowDelta("email_recipient", 0, None, "backfill does not touch recipients"),
                RowDelta(
                    "email_attachment",
                    None,
                    None,
                    "deliberately unconstrained: ruling (c) requires exactly one NEW published "
                    "result per affected canonical input, but WHICH table holds a published "
                    "result version is a stage-C declaration that does not exist yet — "
                    "asserting a row count here would encode an implementation guess. The "
                    "requirement lives in `expected.summary` and `result_version_expectation` "
                    "until the result table is declared",
                ),
                RowDelta("acl_grant", 0, None, "a version backfill changes no permission"),
                RowDelta("person", 0, None, "a version backfill creates no principal"),
            ),
            must_remain_unchanged=("email_attachment.extractor_version@v1",),
            result_version_expectation=(
                "Exactly ONE new published result for the canonical input m1, carrying "
                "extractor version v2; zero further new results; and the pre-existing v1 "
                "result still presented as processed by v1, never restamped as v2."
            ),
        ),
        replay_step(M013, "s3", "Replay after the backfill; the v2 result is not republished."),
    ),
    stage_available="C",
    prebound_identities=(ANNA, BORIS),
    notes=(
        "Record shape only. Every tracked-table delta the criterion actually constrains is "
        "zero; the one-new-version requirement is carried by result_version_expectation "
        "because the table that holds a published result version is a stage-C declaration."
    ),
)


IDEM_SCENARIOS: Final[tuple[IdemScenario, ...]] = (
    SCENARIO_001,
    SCENARIO_002,
    SCENARIO_003,
    SCENARIO_004,
    SCENARIO_005,
    SCENARIO_006,
    SCENARIO_007,
    SCENARIO_008,
    SCENARIO_009,
    SCENARIO_010,
    SCENARIO_011,
    SCENARIO_012,
    SCENARIO_013,
)
"""The IDEM battery: twelve stage-A scenarios plus one stage-C backfill record shape."""
