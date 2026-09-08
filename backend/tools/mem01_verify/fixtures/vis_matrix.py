"""
Role: the VIS (visibility) fixture battery's public face — the PROBE set (one read attempt per
      route x state x reader, each with an independently specified allowed/denied expectation),
      the route x state coverage cells, and `build_vis_matrix()`, the single entry point the VIS
      gate evaluator imports. Record shapes and the org/persona roster live in `vis_matrix_a`;
      the messages, recipients, attachments, grants and promotion lineage live in `vis_matrix_b`.
Used by: the VIS gate evaluator (`tools.mem01_verify.gates.gate_vis`), the fixtures digest
      (`tools.mem01_verify.fixtures.digest`), and the probe-database loader that arranges the
      matrix through the real write plane before reading it through the real reader plane.
Depends on: tools.mem01_verify.fixtures.vis_matrix_a, tools.mem01_verify.fixtures.vis_matrix_b,
      and tools.mem01_verify.exceptions (`FixtureError` — a malformed battery refuses in the
      instrument's own error family, never as a builtin).
      Nothing from `app.*`: an expectation here is never obtained by running a measured component
      (contract R12) — it is read off the VIS criterion sheet, PF-01's grant/promotion rules, and
      the synthetic messages declared in `vis_matrix_b`.
Key invariants:
  - Every probe's `expected` is exactly "allowed" or "denied" and was authored from the rule
    named in its `origin`, never from observing a reader. Where the code is wrong today the
    fixture is expected to FAIL — that is the instrument working.
  - Every stage-A route x state cell (direct_read / lexical_search / attachment_metadata x the
    eight states) carries at least one allowed AND one denied probe: deny-all is a FAIL, and a
    negative control without a positive control in the same cell proves nothing.
  - The direct_read route is probed on ALL THREE tables it covers — email_message,
    email_recipient and email_attachment — in every state; the coverage cells are route x
    state, so they cannot enforce that on their own.
  - Every persona (A1, A2, A3, B1) and the no-person reader has at least one allowed probe.
  - thread_expansion and vector_search cells are LISTED with stage_available "C" / "D" and carry
    zero probes; a gate must print `incomplete` for them, never PASS (contract R3).
  - criterion_id is assigned by one declared rule: a probe on a child row (email_recipient /
    email_attachment) pins `vis.no_wrong_inherited_relations`; a probe on the message row pins
    `vis.no_missing_allowed` when allowed and `vis.no_forbidden_rows` when denied.
  - Every probe names its reader's tenant explicitly in `reader_org_key`; the reader plane
    takes (org_id, person_id) and the no-person reader has no persona to derive an org from.
  - case_id is unique across the battery: probes occupy vis-101..vis-204 and route x state
    cells occupy vis-301..vis-340 (the arrangement ranges are listed in `vis_matrix_a`).
"""

from __future__ import annotations

from tools.mem01_verify.exceptions import FixtureError
from tools.mem01_verify.fixtures.vis_matrix_a import (
    BATTERY,
    CRITERION_IDS,
    CRITERION_NO_FORBIDDEN,
    CRITERION_NO_MISSING,
    CRITERION_NO_WRONG_INHERITED,
    FIXTURES_VERSION,
    NO_PERSON,
    ORGS,
    PERSONAS,
    ROUTE_STAGE,
    ROUTES,
    STAGE_A_ROUTES,
    STATES,
    TARGET_PREFIXES,
    AttachmentSpec,
    GrantSpec,
    MessageSpec,
    OrgSpec,
    Outcome,
    PersonaSpec,
    Probe,
    PromotionSpec,
    RecipientSpec,
    RouteStateCell,
    TargetKind,
    VisMatrix,
)
from tools.mem01_verify.fixtures.vis_matrix_b import (
    MARKER_A01,
    MARKER_A02,
    MARKER_A03,
    MARKER_A04,
    MARKER_A05,
    MARKER_A06,
    MARKER_B01,
    MESSAGES,
)

# --- short aliases used by the probe literals -------------------------------------------------
DIRECT = "direct_read"
LEXICAL = "lexical_search"
CARRIER = "attachment_metadata"
S_GRANTED = "restricted_with_grant"
S_UNGRANTED = "restricted_without_grant"
S_ORG = "org_visible"
S_REVOKED = "grant_revoked"
S_NOPERSON = "no_person"
S_BCC = "bcc_only"
S_POOLED = "pooled_session_reuse"
S_SHARED = "shared_attachment_cross_org"
ALLOWED: Outcome = "allowed"
DENIED: Outcome = "denied"

# persona key -> the tenant that persona reads in (derived from the roster, not from the system).
PERSONA_ORG: dict[str, str] = {p.persona_key: p.org_key for p in PERSONAS}

# --- origins: the rule each probe pins ---------------------------------------------------------
O_LIVE = "a live acl_grant admits exactly its holder to the restricted row it names"
O_UNGRANTED = "no live grant on a restricted row => the RESTRICTIVE visibility policy hides it"
O_ORG_SCOPE = "an org-visible row (with promotion lineage) is readable by every person in its org"
O_CROSS_ORG = "org_isolation RLS: no read ever crosses org_id, whatever the row's visibility"
O_REVOKED = "revocation is a tombstone: revoked_at set => the grant stops matching immediately"
O_NP_OPEN = "a reader with no person bound sees exactly the org-visible rows (not zero rows)"
O_NP_SHUT = "a reader with no person bound sees zero restricted rows"
O_BCC_HIDDEN = "BCC membership never appears in any row a non-BCC persona can read"
O_BCC_PARENT = "the blind-copied person holds no grant, so the restricted parent stays hidden"
O_CHILD_IN = "a child row is visible when — and only when — its parent message is visible"
O_CHILD_OUT = "a child row is never visible to a reader who cannot see its parent message"
O_POOLED = "a reused pooled connection must rebind the person GUC; no reader state survives"
O_SHARED_HASH = "identical attachment bytes in two orgs are two rows; the hash is not a bridge"


def _target_org_key(target_key: str) -> str:
    """Return the org letter a target key names (the `<PREFIX>-<ORG>-NN` key form)."""
    return target_key.split("-")[1]


def _reader_org_key(reader: str, target_key: str) -> str:
    """Return the tenant the reader session is opened for.

    A persona reads in its own org (so a probe on another org's row is a cross-tenant control).
    The no-person reader has no persona, so its session is opened for the target row's own org —
    the strictest arrangement, since a denial there cannot be explained by tenant isolation.
    """
    if reader == NO_PERSON:
        return _target_org_key(target_key)
    return PERSONA_ORG[reader]


def _target_kind(target_key: str) -> TargetKind:
    """Map a fixture target key to the table it names, by this battery's key convention.

    Raises:
        FixtureError: the key carries no known prefix — a malformed battery, never scored.
    """
    for prefix, kind in TARGET_PREFIXES.items():
        if target_key.startswith(prefix):
            return kind
    raise FixtureError(f"unknown fixture target key prefix: {target_key}")


def _criterion_for(target_kind: TargetKind, expected: Outcome) -> str:
    """Assign the vis.* criterion a probe pins (the rule declared in the module docstring)."""
    if target_kind != "email_message":
        return CRITERION_NO_WRONG_INHERITED
    return CRITERION_NO_MISSING if expected == ALLOWED else CRITERION_NO_FORBIDDEN


def _p(
    case_id: str,
    route: str,
    state: str,
    reader: str,
    target_key: str,
    expected: Outcome,
    origin: str,
    *,
    query: str | None = None,
    after: str | None = None,
) -> Probe:
    """Build one probe; `query` carries the lexical marker, `after` the pooled predecessor."""
    kind = _target_kind(target_key)
    return Probe(
        case_id=case_id,
        criterion_id=_criterion_for(kind, expected),
        origin=origin,
        expected=expected,
        route=route,
        state=state,
        reader=reader,
        reader_org_key=_reader_org_key(reader, target_key),
        target_key=target_key,
        target_kind=kind,
        lexical_query=query,
        pooled_predecessor=after,
    )


PROBES: tuple[Probe, ...] = (
    # --- direct_read x restricted_with_grant ---
    _p("vis-101", DIRECT, S_GRANTED, "A1", "M-A-01", ALLOWED, O_LIVE),
    _p("vis-102", DIRECT, S_GRANTED, "A2", "M-A-01", ALLOWED, O_LIVE),
    _p("vis-103", DIRECT, S_GRANTED, "A3", "M-A-01", DENIED, O_UNGRANTED),
    _p("vis-104", DIRECT, S_GRANTED, "B1", "M-A-01", DENIED, O_CROSS_ORG),
    _p("vis-105", DIRECT, S_GRANTED, "A1", "R-A-01-to-A1", ALLOWED, O_CHILD_IN),
    _p("vis-106", DIRECT, S_GRANTED, "A3", "R-A-01-cc-A2", DENIED, O_CHILD_OUT),
    # --- direct_read x restricted_without_grant ---
    _p("vis-107", DIRECT, S_UNGRANTED, "A1", "M-A-02", ALLOWED, O_LIVE),
    _p("vis-108", DIRECT, S_UNGRANTED, "A2", "M-A-02", DENIED, O_UNGRANTED),
    _p("vis-109", DIRECT, S_UNGRANTED, "A3", "M-A-02", DENIED, O_UNGRANTED),
    _p("vis-110", DIRECT, S_UNGRANTED, "B1", "M-A-02", DENIED, O_CROSS_ORG),
    _p("vis-111", DIRECT, S_UNGRANTED, "A1", "R-A-02-to-ext", ALLOWED, O_CHILD_IN),
    _p("vis-112", DIRECT, S_UNGRANTED, "A2", "R-A-02-to-ext", DENIED, O_CHILD_OUT),
    # --- direct_read x org_visible ---
    _p("vis-113", DIRECT, S_ORG, "A1", "M-A-03", ALLOWED, O_ORG_SCOPE),
    _p("vis-114", DIRECT, S_ORG, "A2", "M-A-03", ALLOWED, O_ORG_SCOPE),
    _p("vis-115", DIRECT, S_ORG, "A3", "M-A-03", ALLOWED, O_ORG_SCOPE),
    _p("vis-116", DIRECT, S_ORG, "B1", "M-A-03", DENIED, O_CROSS_ORG),
    _p("vis-117", DIRECT, S_ORG, "A1", "M-B-02", DENIED, O_CROSS_ORG),
    _p("vis-118", DIRECT, S_ORG, "A3", "R-A-03-to-A2", ALLOWED, O_CHILD_IN),
    _p("vis-119", DIRECT, S_ORG, "B1", "R-A-03-to-A2", DENIED, O_CROSS_ORG),
    # --- direct_read x grant_revoked ---
    _p("vis-120", DIRECT, S_REVOKED, "A1", "M-A-04", ALLOWED, O_LIVE),
    _p("vis-121", DIRECT, S_REVOKED, "A2", "M-A-04", DENIED, O_REVOKED),
    _p("vis-122", DIRECT, S_REVOKED, "A3", "M-A-04", DENIED, O_UNGRANTED),
    _p("vis-123", DIRECT, S_REVOKED, "A1", "R-A-04-to-A2", ALLOWED, O_CHILD_IN),
    _p("vis-124", DIRECT, S_REVOKED, "A2", "R-A-04-to-A2", DENIED, O_REVOKED),
    # --- direct_read x no_person ---
    _p("vis-125", DIRECT, S_NOPERSON, NO_PERSON, "M-A-03", ALLOWED, O_NP_OPEN),
    _p("vis-126", DIRECT, S_NOPERSON, NO_PERSON, "R-A-03-to-A2", ALLOWED, O_NP_OPEN),
    _p("vis-127", DIRECT, S_NOPERSON, NO_PERSON, "M-A-01", DENIED, O_NP_SHUT),
    _p("vis-128", DIRECT, S_NOPERSON, NO_PERSON, "M-A-02", DENIED, O_NP_SHUT),
    _p("vis-129", DIRECT, S_NOPERSON, NO_PERSON, "M-A-04", DENIED, O_NP_SHUT),
    _p("vis-130", DIRECT, S_NOPERSON, NO_PERSON, "R-A-05-bcc-A3", DENIED, O_BCC_HIDDEN),
    # --- direct_read x bcc_only ---
    _p("vis-131", DIRECT, S_BCC, "A1", "M-A-05", ALLOWED, O_LIVE),
    _p("vis-132", DIRECT, S_BCC, "A2", "M-A-05", ALLOWED, O_LIVE),
    _p("vis-133", DIRECT, S_BCC, "A3", "M-A-05", DENIED, O_BCC_PARENT),
    _p("vis-134", DIRECT, S_BCC, "B1", "M-A-05", DENIED, O_CROSS_ORG),
    _p("vis-135", DIRECT, S_BCC, "A1", "R-A-05-to-A2", ALLOWED, O_CHILD_IN),
    _p("vis-136", DIRECT, S_BCC, "A1", "R-A-05-bcc-A3", DENIED, O_BCC_HIDDEN),
    _p("vis-137", DIRECT, S_BCC, "A2", "R-A-05-bcc-A3", DENIED, O_BCC_HIDDEN),
    _p("vis-138", DIRECT, S_BCC, "A3", "R-A-05-bcc-A3", DENIED, O_BCC_PARENT),
    # --- direct_read x pooled_session_reuse ---
    _p("vis-139", DIRECT, S_POOLED, "A3", "M-A-01", DENIED, O_POOLED, after="A1"),
    _p("vis-140", DIRECT, S_POOLED, "A3", "M-A-03", ALLOWED, O_POOLED, after="A1"),
    _p("vis-141", DIRECT, S_POOLED, "A1", "M-A-02", ALLOWED, O_POOLED, after="A3"),
    _p("vis-142", DIRECT, S_POOLED, NO_PERSON, "M-A-01", DENIED, O_POOLED, after="A1"),
    _p("vis-143", DIRECT, S_POOLED, "A2", "R-A-05-bcc-A3", DENIED, O_POOLED, after="A1"),
    # --- direct_read x shared_attachment_cross_org ---
    _p("vis-144", DIRECT, S_SHARED, "A1", "M-A-06", ALLOWED, O_LIVE),
    _p("vis-145", DIRECT, S_SHARED, "B1", "M-A-06", DENIED, O_SHARED_HASH),
    _p("vis-146", DIRECT, S_SHARED, "B1", "M-B-01", ALLOWED, O_LIVE),
    _p("vis-147", DIRECT, S_SHARED, "A1", "M-B-01", DENIED, O_SHARED_HASH),
    # --- direct_read x email_attachment (the third table the direct_read route covers; the
    #     attachment_metadata route reads the SAME rows through the carrier-metadata path, so
    #     both are probed) ---
    _p("vis-187", DIRECT, S_GRANTED, "A2", "ATT-A-01", ALLOWED, O_CHILD_IN),
    _p("vis-188", DIRECT, S_GRANTED, "A3", "ATT-A-01", DENIED, O_CHILD_OUT),
    _p("vis-189", DIRECT, S_UNGRANTED, "A1", "ATT-A-02", ALLOWED, O_CHILD_IN),
    _p("vis-190", DIRECT, S_UNGRANTED, "A2", "ATT-A-02", DENIED, O_CHILD_OUT),
    _p("vis-191", DIRECT, S_ORG, "A3", "ATT-A-03", ALLOWED, O_CHILD_IN),
    _p("vis-192", DIRECT, S_ORG, "B1", "ATT-A-03", DENIED, O_CROSS_ORG),
    _p("vis-193", DIRECT, S_REVOKED, "A1", "ATT-A-04", ALLOWED, O_CHILD_IN),
    _p("vis-194", DIRECT, S_REVOKED, "A2", "ATT-A-04", DENIED, O_REVOKED),
    _p("vis-195", DIRECT, S_NOPERSON, NO_PERSON, "ATT-A-03", ALLOWED, O_NP_OPEN),
    _p("vis-196", DIRECT, S_NOPERSON, NO_PERSON, "ATT-A-01", DENIED, O_NP_SHUT),
    _p("vis-197", DIRECT, S_BCC, "A2", "ATT-A-05", ALLOWED, O_CHILD_IN),
    _p("vis-198", DIRECT, S_BCC, "A3", "ATT-A-05", DENIED, O_BCC_PARENT),
    _p("vis-199", DIRECT, S_POOLED, "A3", "ATT-A-03", ALLOWED, O_POOLED, after="A1"),
    _p("vis-200", DIRECT, S_POOLED, "A3", "ATT-A-01", DENIED, O_POOLED, after="A1"),
    _p("vis-201", DIRECT, S_SHARED, "A1", "ATT-A-06", ALLOWED, O_CHILD_IN),
    _p("vis-202", DIRECT, S_SHARED, "B1", "ATT-A-06", DENIED, O_SHARED_HASH),
    _p("vis-203", DIRECT, S_SHARED, "A1", "R-A-06-to-ext", ALLOWED, O_CHILD_IN),
    _p("vis-204", DIRECT, S_SHARED, "B1", "R-A-06-to-ext", DENIED, O_CROSS_ORG),
    # --- lexical_search (body substring through the reader plane) ---
    _p("vis-148", LEXICAL, S_GRANTED, "A2", "M-A-01", ALLOWED, O_LIVE, query=MARKER_A01),
    _p("vis-149", LEXICAL, S_GRANTED, "A3", "M-A-01", DENIED, O_UNGRANTED, query=MARKER_A01),
    _p("vis-150", LEXICAL, S_GRANTED, "B1", "M-A-01", DENIED, O_CROSS_ORG, query=MARKER_A01),
    _p("vis-151", LEXICAL, S_UNGRANTED, "A1", "M-A-02", ALLOWED, O_LIVE, query=MARKER_A02),
    _p("vis-152", LEXICAL, S_UNGRANTED, "A2", "M-A-02", DENIED, O_UNGRANTED, query=MARKER_A02),
    _p("vis-153", LEXICAL, S_ORG, "A3", "M-A-03", ALLOWED, O_ORG_SCOPE, query=MARKER_A03),
    _p("vis-154", LEXICAL, S_ORG, "B1", "M-A-03", DENIED, O_CROSS_ORG, query=MARKER_A03),
    _p("vis-155", LEXICAL, S_REVOKED, "A1", "M-A-04", ALLOWED, O_LIVE, query=MARKER_A04),
    _p("vis-156", LEXICAL, S_REVOKED, "A2", "M-A-04", DENIED, O_REVOKED, query=MARKER_A04),
    _p("vis-157", LEXICAL, S_NOPERSON, NO_PERSON, "M-A-03", ALLOWED, O_NP_OPEN, query=MARKER_A03),
    _p("vis-158", LEXICAL, S_NOPERSON, NO_PERSON, "M-A-04", DENIED, O_NP_SHUT, query=MARKER_A04),
    _p("vis-159", LEXICAL, S_BCC, "A2", "M-A-05", ALLOWED, O_LIVE, query=MARKER_A05),
    _p("vis-160", LEXICAL, S_BCC, "A3", "M-A-05", DENIED, O_BCC_PARENT, query=MARKER_A05),
    _p(
        "vis-161", LEXICAL, S_POOLED, "A3", "M-A-01", DENIED, O_POOLED, query=MARKER_A01, after="A1"
    ),
    _p(
        "vis-162",
        LEXICAL,
        S_POOLED,
        "A3",
        "M-A-03",
        ALLOWED,
        O_POOLED,
        query=MARKER_A03,
        after="A1",
    ),
    _p("vis-163", LEXICAL, S_SHARED, "A1", "M-A-06", ALLOWED, O_LIVE, query=MARKER_A06),
    _p("vis-164", LEXICAL, S_SHARED, "B1", "M-A-06", DENIED, O_CROSS_ORG, query=MARKER_A06),
    _p("vis-165", LEXICAL, S_SHARED, "B1", "M-B-01", ALLOWED, O_LIVE, query=MARKER_B01),
    _p("vis-166", LEXICAL, S_SHARED, "A1", "M-B-01", DENIED, O_CROSS_ORG, query=MARKER_B01),
    # --- attachment_metadata (carrier row: filename, content_type, size, hash, status) ---
    _p("vis-167", CARRIER, S_GRANTED, "A2", "ATT-A-01", ALLOWED, O_CHILD_IN),
    _p("vis-168", CARRIER, S_GRANTED, "A3", "ATT-A-01", DENIED, O_CHILD_OUT),
    _p("vis-169", CARRIER, S_GRANTED, "B1", "ATT-A-01", DENIED, O_CROSS_ORG),
    _p("vis-170", CARRIER, S_UNGRANTED, "A1", "ATT-A-02", ALLOWED, O_CHILD_IN),
    _p("vis-171", CARRIER, S_UNGRANTED, "A2", "ATT-A-02", DENIED, O_CHILD_OUT),
    _p("vis-172", CARRIER, S_ORG, "A3", "ATT-A-03", ALLOWED, O_CHILD_IN),
    _p("vis-173", CARRIER, S_ORG, "B1", "ATT-A-03", DENIED, O_CROSS_ORG),
    _p("vis-174", CARRIER, S_REVOKED, "A1", "ATT-A-04", ALLOWED, O_CHILD_IN),
    _p("vis-175", CARRIER, S_REVOKED, "A2", "ATT-A-04", DENIED, O_REVOKED),
    _p("vis-176", CARRIER, S_NOPERSON, NO_PERSON, "ATT-A-03", ALLOWED, O_NP_OPEN),
    _p("vis-177", CARRIER, S_NOPERSON, NO_PERSON, "ATT-A-01", DENIED, O_NP_SHUT),
    _p("vis-178", CARRIER, S_BCC, "A2", "ATT-A-05", ALLOWED, O_CHILD_IN),
    _p("vis-179", CARRIER, S_BCC, "A3", "ATT-A-05", DENIED, O_BCC_PARENT),
    _p("vis-180", CARRIER, S_POOLED, "A3", "ATT-A-01", DENIED, O_POOLED, after="A1"),
    _p("vis-181", CARRIER, S_POOLED, "A3", "ATT-A-03", ALLOWED, O_POOLED, after="A1"),
    _p("vis-182", CARRIER, S_SHARED, "A1", "ATT-A-06", ALLOWED, O_CHILD_IN),
    _p("vis-183", CARRIER, S_SHARED, "B1", "ATT-A-06", DENIED, O_SHARED_HASH),
    _p("vis-184", CARRIER, S_SHARED, "B1", "ATT-B-01", ALLOWED, O_CHILD_IN),
    _p("vis-185", CARRIER, S_SHARED, "A1", "ATT-B-01", DENIED, O_SHARED_HASH),
    _p("vis-186", CARRIER, S_SHARED, "A2", "ATT-B-01", DENIED, O_SHARED_HASH),
)

_CELL_ORIGIN_STAGE_A = (
    "criterion sheet VIS: every route x state cell needs >=1 positive AND >=1 negative control; "
    "deny-all is a FAIL and a lone negative control proves nothing"
)
_CELL_ORIGIN_LATER = (
    "contract 10.1 / criteria route_state_matrix: the cell is listed so the gate can print "
    "`incomplete` for a route whose implementation does not exist yet (R3), never PASS"
)


def _build_cells(probes: tuple[Probe, ...]) -> tuple[RouteStateCell, ...]:
    """Group the probes into the full route x state matrix, listing later-stage cells empty."""
    cells: list[RouteStateCell] = []
    for route in ROUTES:
        stage = ROUTE_STAGE[route]
        for state in STATES:
            covering = tuple(p for p in probes if p.route == route and p.state == state)
            allowed = sum(1 for p in covering if p.expected == ALLOWED)
            is_stage_a = stage == "A"
            cells.append(
                RouteStateCell(
                    case_id=f"vis-{300 + len(cells) + 1:03d}",
                    criterion_id=CRITERION_NO_MISSING,
                    origin=_CELL_ORIGIN_STAGE_A if is_stage_a else _CELL_ORIGIN_LATER,
                    expected=(
                        "at_least_one_allowed_and_one_denied_probe"
                        if is_stage_a
                        else f"no_probes_until_stage_{stage}"
                    ),
                    route=route,
                    state=state,
                    stage_available=stage,
                    probe_case_ids=tuple(p.case_id for p in covering),
                    allowed_count=allowed,
                    denied_count=len(covering) - allowed,
                )
            )
    return tuple(cells)


def build_vis_matrix() -> VisMatrix:
    """Return the whole VIS battery: roster, arrangement, probes and coverage cells.

    Contract:
        Pure and deterministic — no I/O, no database, no randomness, no measured component. Two
        calls return equal values, so the battery contributes a stable fixtures digest.

    Returns:
        VisMatrix: two orgs, four personas, eight messages with their children/grants/lineage,
        the probe set, and all len(ROUTES) x len(STATES) coverage cells (later-stage cells
        present with zero probes and their stage letter).
    """
    return VisMatrix(
        version=FIXTURES_VERSION,
        battery=BATTERY,
        orgs=ORGS,
        personas=PERSONAS,
        messages=MESSAGES,
        probes=PROBES,
        cells=_build_cells(PROBES),
    )


__all__ = [
    "ALLOWED",
    "BATTERY",
    "CRITERION_IDS",
    "CRITERION_NO_FORBIDDEN",
    "CRITERION_NO_MISSING",
    "CRITERION_NO_WRONG_INHERITED",
    "DENIED",
    "FIXTURES_VERSION",
    "MESSAGES",
    "NO_PERSON",
    "ORGS",
    "PERSONAS",
    "PROBES",
    "ROUTES",
    "ROUTE_STAGE",
    "STAGE_A_ROUTES",
    "STATES",
    "AttachmentSpec",
    "GrantSpec",
    "MessageSpec",
    "OrgSpec",
    "PersonaSpec",
    "Probe",
    "PromotionSpec",
    "RecipientSpec",
    "RouteStateCell",
    "VisMatrix",
    "build_vis_matrix",
]
