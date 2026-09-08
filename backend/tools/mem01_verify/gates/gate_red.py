"""
Role: The RED gate (secret redaction) of contract §11 — it passes every `fixtures.red_cases`
      canary through the REAL Stage-A handoffs a secret can travel (the body redactor, the
      attachment text path, the structured `extracted_data` payload, the stored extraction
      detail and the ingest log output) and scores what came back with the pure `score_canary` /
      `score_negative` of §16.11. Stage A is expected to FAIL: canaries past the 2,000,000-scalar
      scan cap survive and the structured payload is never masked.
Used by: `tools.mem01_verify.gates.registry` (`evaluate`); sealed by
      `tests/tools/mem01_verify/test_gate_scoring.py` (the pure surfaces) and
      `test_gates_stage_a.py` (`red.no_under_redaction` FAIL with a non-zero numerator).
Depends on: `tools.mem01_verify.app_log_names` (the `app` logger name and the log format, the
      leaf `runner_logging` shares — B11: a gate never imports a `runner_*` module),
      `.gates.context`, `.criteria`, `.statuses`, `.fixtures.red_cases`
      (canaries, controls and the surface matrix — expectations only, R12),
      `.fixtures.fid_builders_b.build_xlsx` (the workbook carrier for the structured surface),
      `.gates.gate_red_carriers` (the degraded-parse carrier of the `logging` surface) and the
      measured components `app.connectors.extraction.redact.redact_secrets`,
      `app.connectors.imap.parsing.attachment_extractor.extract_text` and
      `app.connectors.imap.parsing.email_parser.parse_email`.
Key invariants:
  - §16.9: `fully_redacted` means no part of the canary survives on ANY surface it travelled; a
    typed placeholder in its place is the approved transformation. The denominator of
    `red.no_under_redaction` is CANARIES, not canary x surface.
  - A survivor is the whole canary OR any contiguous fragment of at least
    `FRAGMENT_MIN_SCALARS` scalars that carries a letter (an all-digit run can occur innocently;
    the fixture's filler carries no identifier-shaped token, so a letter-bearing fragment cannot).
  - A control is scored only on surfaces that actually carried it: an empty (or absent) surface
    output means the control never reached that handoff, which is not over-redaction.
  - `score_canary` and `score_negative` are PURE; `evaluate` routes every case through them.
  - §16.17(a): the log collector listens on the ROOT logger AND on the `app` logger — under
    the runner's capture `app` does not propagate — formats a record the way `app.log` does, and
    counts a record delivered to both loggers once.
  - §16.17(b): the `logging` surface is EXERCISED, never assumed. Every positive canary's whole
    text additionally travels the parser's degraded-parse handoff; a hard negative travels the
    four text-carrying surfaces only, because a log line echoes no control. That output is NOT a
    positional transform of the case text, so the fragment scan runs over the WHOLE `logging`
    output however long it is; the region rule below belongs to the four text-carrying surfaces
    alone, whose output keeps the case text's coordinates. A Stage-A surface no
    positive canary produced output on is named in `diagnostics.surfaces_unexercised`, dropped
    from `surfaces_scored`, and makes `red.no_under_redaction` `incomplete` — never a vacuous
    PASS.
  - The canary text itself NEVER reaches stdout, a reason string or a diagnostic (R5): case rows
    carry case ids, surface names and counts only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from hashlib import sha256

from tools.mem01_verify.app_log_names import APP_LOG_FORMAT, APP_LOGGER_NAME

# `LOGGING_SURFACE_NAME` and `TEXT_SURFACE_NAMES` come from the declaring module: the public
# re-export `fixtures.red_cases` is outside this change's file set and is left untouched.
from tools.mem01_verify.fixtures.red_cases import (
    RED_NEGATIVES,
    RED_POSITIVES,
    RED_SURFACES,
    STAGE_A_SURFACE_NAMES,
    RedNegative,
    RedPositive,
)
from tools.mem01_verify.fixtures.red_cases_b import (
    LOGGING_SURFACE_NAME,
    TEXT_SURFACE_NAMES,
)
from tools.mem01_verify.gates.context import (
    CaseVerdict,
    GateContext,
    GateResult,
    criterion_entry,
    write_gate_report,
)
from tools.mem01_verify.gates.gate_red_carriers import push_through_degraded_parse
from tools.mem01_verify.statuses import INCOMPLETE, derive_gate_status

GATE = "RED"
#: §12: this gate never touches `ctx.probe`, so it alone never leases a probe.
NEEDS_PROBE: bool = False
UNDER_CRITERION_ID = "red.no_under_redaction"
OVER_CRITERION_ID = "red.no_over_redaction"

#: The shortest surviving fragment that counts as under-redaction (§16.9 "no part survives").
FRAGMENT_MIN_SCALARS = 8
#: Outputs at or below this length are scanned for fragments in full.
REGION_SCAN_LIMIT = 200_000
#: Scalars of slack around the canary's declared span when a long output is scanned locally.
REGION_MARGIN = 8_192
#: The content types the two attachment carriers declare.
TEXT_CONTENT_TYPE = "text/plain"
WORKBOOK_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The surfaces whose output IS a positional transform of the case text, so the region rule may
#: narrow a long scan there (§16.17(b)). Any other surface — `logging` today, and any surface
#: added later until it is declared positional — is scanned whole, however long its output.
_POSITIONAL_SURFACE_NAMES = frozenset(TEXT_SURFACE_NAMES)
#: The positive canaries, by case id: only they travel the degraded-parse carrier (§16.17(b)).
_CARRIER_CASE_IDS = frozenset(canary.case_id for canary in RED_POSITIVES)

_GATE_REASON = (
    "secret canaries are pushed through every Stage-A handoff; the embedding-header surface has "
    "no implementation before stage D and is recorded, never scored"
)
_SURFACE_NOTE = "surface outputs are taken from the real handoffs, one pass per case"
_UNEXERCISED_REASON = (
    "not decided: no positive canary produced output on {surfaces}; an unexercised Stage-A "
    "surface is never evidence of redaction"
)


def _fragments(secret: str) -> tuple[str, ...]:
    """Return the letter-bearing windows whose survival counts as a partial leak (§16.9)."""
    windows = (
        secret[index : index + FRAGMENT_MIN_SCALARS]
        for index in range(len(secret) - FRAGMENT_MIN_SCALARS + 1)
    )
    return tuple(window for window in windows if any(char.isalpha() for char in window))


def _scan_region(output: str, span: tuple[int, int], *, positional: bool) -> str:
    """Return the slice of a surface output a surviving fragment could occupy.

    A short output is scanned whole, and so is a NON-positional surface's output at any length:
    `logging` renders the ingest path's own records rather than the case text, so the canary's
    declared coordinates say nothing about where a fragment of it may land there (§16.17(b)).
    A long output of a POSITIONAL surface (the beyond-cap canaries carry two million scalars of
    filler) is scanned around the declared span with generous slack, because on that surface
    redaction only ever substitutes in place and cannot move a survivor further than that.
    """
    if not positional or len(output) <= REGION_SCAN_LIMIT:
        return output
    low = max(0, span[0] - REGION_MARGIN)
    high = min(len(output), span[1] + REGION_MARGIN)
    return output[low:high]


def _survives(secret: str, span: tuple[int, int], output: str, *, positional: bool) -> bool:
    """True when the secret, or a letter-bearing fragment of it, is still present in `output`.

    `positional` says whether this surface's output preserves the case text's coordinates; when
    it does not, the fragment scan covers the whole output however long it is (§16.17(b)).
    """
    if not output:
        return False
    if secret in output:
        return True
    region = _scan_region(output, span, positional=positional)
    return any(fragment in region for fragment in _fragments(secret))


def score_canary(canary: RedPositive, surface_outputs: Mapping[str, str]) -> CaseVerdict:
    """Score one positive canary against what every surface it travelled returned (§16.11, pure).

    Args:
        canary: The fixture record — the canary text, its declared span and its surfaces.
        surface_outputs: Surface name → the text that surface produced for this canary. A
            surface absent from the mapping was not available on this run and is recorded as a
            defect: an unscored surface is never evidence of redaction.

    Contract:
        Passes iff no surface's output still carries the canary in whole or in part.

    Returns:
        A `CaseVerdict` whose defects name the surfaces that leaked — never the secret (R5).
    """
    defects: list[str] = []
    for surface in canary.surfaces:
        if surface not in surface_outputs:
            defects.append(f"surface_missing:{surface}")
            continue
        if _survives(
            canary.canary_text,
            canary.canary_span,
            surface_outputs[surface],
            positional=surface in _POSITIONAL_SURFACE_NAMES,
        ):
            defects.append(f"survived_on:{surface}")
    return CaseVerdict(canary.case_id, canary.criterion_id, not defects, tuple(defects))


def score_negative(control: RedNegative, surface_outputs: Mapping[str, str]) -> CaseVerdict:
    """Score one hard-negative control: it must survive every surface that carried it (pure).

    Args:
        control: The fixture record — a protected non-secret span and its surfaces.
        surface_outputs: Surface name → the text that surface produced. An empty or absent
            output means the surface never carried the control (the stored extraction detail
            carries no attachment content by contract), so that surface is not scored.

    Contract:
        Passes iff the control text occurs verbatim in every non-empty surface output, and at
        least one surface carried it.

    Returns:
        A `CaseVerdict` whose defects name the surfaces that altered the control.
    """
    defects: list[str] = []
    scored = 0
    for surface in control.surfaces:
        output = surface_outputs.get(surface, "")
        if not output:
            continue
        scored += 1
        if control.control_text not in output:
            defects.append(f"altered_on:{surface}")
    if scored == 0:
        defects.append("no_surface_carried_the_control")
    return CaseVerdict(control.case_id, control.criterion_id, not defects, tuple(defects))


@contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Collect every log record emitted while the block runs — the `logging` surface's output.

    The collector listens on the ROOT logger and on the `app` logger (§16.17(a)): under the
    runner's capture `app` does not propagate, so a root-only listener would hear nothing an
    application module logged and the surface would be silently empty. It is APPENDED to the
    `app` handlers, never substituted for them, so the run's own capture keeps receiving; both
    handler lists and both levels are restored on exit. A record delivered to both loggers is
    collected ONCE — the seen records are held by reference, so no freed record's `id` is reused.
    """
    lines: list[str] = []
    seen: dict[int, logging.LogRecord] = {}
    formatter = logging.Formatter(APP_LOG_FORMAT)

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if id(record) in seen:
                return
            seen[id(record)] = record
            lines.append(formatter.format(record))

    handler = _Collector(level=logging.DEBUG)
    root, app = logging.getLogger(), logging.getLogger(APP_LOGGER_NAME)
    previous_root_level, previous_app_level = root.level, app.level
    app_handlers = list(app.handlers)
    # Every OTHER root handler is muted while we listen: raising the root to DEBUG would
    # otherwise push the ingest path's own records onto the console, and stdout/stderr belong
    # to the machine block alone (R5).
    muted = [(existing, existing.level) for existing in root.handlers]
    for existing, _ in muted:
        existing.setLevel(logging.CRITICAL + 1)
    root.addHandler(handler)
    app.addHandler(handler)
    root.setLevel(logging.DEBUG)
    app.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        app.handlers = app_handlers
        root.setLevel(previous_root_level)
        app.setLevel(previous_app_level)
        for existing, level in muted:
            existing.setLevel(level)


def _parsed_attachment(name: str, content_type: str, payload: bytes) -> object:
    """Build the ParsedAttachment the extractor handoff takes (imported lazily, app-side type)."""
    from app.connectors.imap.parsing.models import ParsedAttachment

    return ParsedAttachment(
        filename=name,
        content_type=content_type,
        size_bytes=len(payload),
        content_hash=sha256(payload).hexdigest(),
        is_inline=False,
        content_id=None,
        payload=payload,
    )


def _surface_outputs(case_id: str, text: str, marker: str) -> dict[str, str]:
    """Push one case's text through every Stage-A handoff and return what each surface produced.

    Args:
        case_id: The fixture case id, used only to name the synthetic carriers.
        text: The full case text, with the secret or control in place.
        marker: The secret or control itself — the value the structured-payload carrier holds in
            one cell, so that surface is exercised with a real workbook rather than a copy of
            the multi-megabyte filler.

    Returns:
        Surface name → output text: the four text-carrying surfaces for every case, plus
        `logging` for a POSITIVE canary, whose text additionally travels the parser's
        degraded-parse handoff (§16.17(b)). A hard-negative control never travels `logging`
        — a log line echoes no control, so "unchanged" is not scoreable there.
    """
    from app.connectors.extraction.redact import redact_secrets
    from app.connectors.imap.parsing.attachment_extractor import extract_text
    from tools.mem01_verify.fixtures.fid_builders_b import build_xlsx

    carries_logging = case_id in _CARRIER_CASE_IDS
    outputs: dict[str, str] = {}
    with _captured_logs() as lines:
        outputs["email_body"] = redact_secrets(text)[0]
        extraction = extract_text(
            _parsed_attachment(f"{case_id}.txt", TEXT_CONTENT_TYPE, text.encode("utf-8"))
        )
        outputs["attachment_text"] = extraction.text or ""
        outputs["extraction_detail"] = extraction.detail or ""
        workbook = build_xlsx((("Sheet1", (("value", marker),)),))
        structured = extract_text(
            _parsed_attachment(f"{case_id}.xlsx", WORKBOOK_CONTENT_TYPE, workbook)
        )
        payload = structured.structured
        outputs["attachment_structured_payload"] = (
            json.dumps(payload, ensure_ascii=False, default=str) if payload else ""
        )
        if carries_logging:
            push_through_degraded_parse(case_id, text)
    if carries_logging:
        outputs[LOGGING_SURFACE_NAME] = "\n".join(lines)
    return outputs


def _score_positives() -> tuple[list[CaseVerdict], dict[str, int], set[str]]:
    """Run every canary through the real surfaces and score it; count leaks, note what ran.

    The third return value is the set of surfaces that produced output for at least one positive
    canary — the evidence the vacuity guard of §16.17(b) decides on.
    """
    verdicts: list[CaseVerdict] = []
    leaks_by_surface: dict[str, int] = {}
    exercised: set[str] = set()
    for canary in RED_POSITIVES:
        outputs = _surface_outputs(canary.case_id, canary.text_builder(), canary.canary_text)
        exercised.update(surface for surface, output in outputs.items() if output)
        verdict = score_canary(canary, outputs)
        verdicts.append(verdict)
        for defect in verdict.defects:
            surface = defect.split(":", 1)[-1]
            leaks_by_surface[surface] = leaks_by_surface.get(surface, 0) + 1
    return verdicts, leaks_by_surface, exercised


def _unexercised_surfaces(exercised: AbstractSet[str]) -> list[str]:
    """The Stage-A `no_canary_survives` surfaces no positive canary reached (§16.17(b)).

    An unexercised surface is never evidence of redaction: it is reported, dropped from the
    scored set, and makes `red.no_under_redaction` `incomplete` rather than a vacuous PASS.
    """
    return sorted(
        surface.name
        for surface in RED_SURFACES
        if surface.stage_available == "A"
        and surface.expected == "no_canary_survives"
        and surface.name not in exercised
    )


def _score_negatives() -> list[CaseVerdict]:
    """Run every hard-negative control through the real surfaces and score it."""
    return [
        score_negative(
            control, _surface_outputs(control.case_id, control.text, control.control_text)
        )
        for control in RED_NEGATIVES
    ]


def _placement_counts(verdicts: Sequence[CaseVerdict]) -> dict[str, int]:
    """Count failing canaries by declared placement — the beyond-cap story, in aggregate only."""
    failed = {verdict.case_id for verdict in verdicts if not verdict.passed}
    counts: dict[str, int] = {}
    for canary in RED_POSITIVES:
        if canary.case_id in failed:
            counts[canary.placement] = counts.get(canary.placement, 0) + 1
    return dict(sorted(counts.items()))


def _entries(
    ctx: GateContext,
    under: Sequence[CaseVerdict],
    over: Sequence[CaseVerdict],
    unexercised: Sequence[str],
) -> list[dict[str, object]]:
    """Build the two §3.4 entries of this gate from the scored canaries and controls.

    `unexercised` names the Stage-A surfaces no positive canary reached: while it is non-empty
    the under-redaction criterion is NOT decided (§16.17(b)) — it is `incomplete`, with a
    reason naming those surfaces, because a surface that produced nothing proves no redaction.
    """
    counts = {
        UNDER_CRITERION_ID: (sum(1 for v in under if not v.passed), len(under)),
        OVER_CRITERION_ID: (sum(1 for v in over if not v.passed), len(over)),
    }
    entries: list[dict[str, object]] = []
    for criterion in ctx.criteria.by_gate.get(GATE, ()):
        if criterion.id == UNDER_CRITERION_ID and unexercised:
            entries.append(
                criterion_entry(
                    criterion,
                    status=INCOMPLETE,
                    reason=_UNEXERCISED_REASON.format(surfaces=", ".join(unexercised)),
                )
            )
            continue
        numerator, denominator = counts[criterion.id]
        entries.append(
            criterion_entry(
                criterion,
                reason=f"{numerator}/{denominator}; {_SURFACE_NOTE}",
                numerator=numerator,
                denominator=denominator,
                expected=denominator,
                evaluated=denominator,
            )
        )
    return entries


async def evaluate(ctx: GateContext) -> GateResult:
    """Return the RED gate result: canaries and controls scored on every Stage-A surface.

    Args:
        ctx: The run context (the battery is public F evidence, so no database is opened).

    Returns:
        A `GateResult` carrying both RED entries, per-surface and per-placement aggregates, and
        the `gates/RED.json` report holding every `CaseVerdict`.
    """
    positives, leaks_by_surface, exercised = _score_positives()
    negatives = _score_negatives()
    unexercised = _unexercised_surfaces(exercised)
    entries = _entries(ctx, positives, negatives, unexercised)
    diagnostics: dict[str, object] = {
        "canaries": len(positives),
        "canaries_leaked": sum(1 for verdict in positives if not verdict.passed),
        "leaks_by_surface": dict(sorted(leaks_by_surface.items())),
        "leaks_by_placement": _placement_counts(positives),
        "controls": len(negatives),
        "controls_altered": sum(1 for verdict in negatives if not verdict.passed),
        "surfaces_scored": [name for name in STAGE_A_SURFACE_NAMES if name not in unexercised],
        "surfaces_unexercised": unexercised,
        "surfaces_incomplete": [
            surface.name for surface in RED_SURFACES if surface.expected == "incomplete"
        ],
    }
    status = derive_gate_status(entries)
    report = write_gate_report(
        ctx.report_dir,
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=entries,
        cases=(*positives, *negatives),
        diagnostics=diagnostics,
    )
    return GateResult(
        name=GATE,
        status=status,
        reason=_GATE_REASON,
        criteria=tuple(entries),
        diagnostics=diagnostics,
        report_files=(report,),
    )


__all__ = [
    "FRAGMENT_MIN_SCALARS",
    "GATE",
    "OVER_CRITERION_ID",
    "UNDER_CRITERION_ID",
    "evaluate",
    "score_canary",
    "score_negative",
]
