"""
Role: the SHAPES half of the VIS (visibility) fixture battery — the frozen record types, the
      controlled vocabularies (routes, states, readers, criterion ids) and the tenant/persona
      roster of an org x persona x route x state visibility matrix. Data only: this module
      declares WHAT a fixture record looks like, WHICH values are legal and WHO reads; it never
      measures anything and never imports an application component.
Used by: tools.mem01_verify.fixtures.vis_matrix_b (the message data),
      tools.mem01_verify.fixtures.vis_matrix (probes, cells, build_vis_matrix), and — through
      that module — the VIS gate evaluator and the fixtures digest.
Depends on: nothing inside the project (stdlib only, by design: a fixture must not be able to
      learn an expectation from the code under test).
Key invariants:
  - Every record type carries case_id / criterion_id / origin / expected (contract 10, R12).
    `expected` on an ARRANGEMENT record (org, persona, message, attachment, grant, promotion)
    states the condition that must hold after the row is loaded through the real write plane;
    `expected` on a Probe is the read outcome and is only ever "allowed" or "denied".
  - Expectations are authored from the criterion sheet (VIS, criteria.step1.v1 vis.*), from
    PF-01's grant/promotion rules and from the synthetic originals declared here. They are NEVER
    obtained by running a reader, a policy, a resolver or any other measured component (R12).
  - case_id is unique across the battery and always of the form `vis-NNN`, allocated in
    reserved ranges so a record's kind is readable from its id: orgs 001-002, personas
    003-006, messages 010-017, recipients 020-029, attachments 030-036, grants 040-050,
    promotions 051-052, probes 101-204, route x state cells 301-340.
  - criterion_id is always one of CRITERION_IDS, which mirrors the vis.* ids in
    release/criteria.step1.v1.yaml. A criterion id that is not in that file is a fixture defect.
  - ROUTES / STATES / ROUTE_STAGE mirror `VIS.route_state_matrix` in the criteria file verbatim;
    if the criteria file changes, this vocabulary changes with it, never the other way round.
  - No real corpus text and no personal data: addresses live only under example.test / acme.test
    / partner.test, names are synthetic, and both Bulgarian and English cases are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

FIXTURES_VERSION = "FIXTURES_V1"
BATTERY = "vis_matrix"

# --- criterion ids (mirror release/criteria.step1.v1.yaml, gate VIS) -------------------------
CRITERION_NO_FORBIDDEN = "vis.no_forbidden_rows"
CRITERION_NO_MISSING = "vis.no_missing_allowed"
CRITERION_NO_WRONG_INHERITED = "vis.no_wrong_inherited_relations"
CRITERION_IDS: tuple[str, ...] = (
    CRITERION_NO_FORBIDDEN,
    CRITERION_NO_MISSING,
    CRITERION_NO_WRONG_INHERITED,
)

# --- route / state vocabulary (VIS.route_state_matrix) ---------------------------------------
ROUTES: tuple[str, ...] = (
    "direct_read",
    "lexical_search",
    "attachment_metadata",
    "thread_expansion",
    "vector_search",
)
STATES: tuple[str, ...] = (
    "restricted_with_grant",
    "restricted_without_grant",
    "org_visible",
    "grant_revoked",
    "no_person",
    "bcc_only",
    "pooled_session_reuse",
    "shared_attachment_cross_org",
)
# The stage at which each route becomes measurable (criteria route_state_matrix).
StageLetter = Literal["A", "B", "C", "D"]
ROUTE_STAGE: dict[str, StageLetter] = {
    "direct_read": "A",
    "lexical_search": "A",
    "attachment_metadata": "A",
    "thread_expansion": "C",
    "vector_search": "D",
}
STAGE_A_ROUTES: tuple[str, ...] = tuple(r for r in ROUTES if ROUTE_STAGE[r] == "A")

# --- readers ---------------------------------------------------------------------------------
# A reader is either a persona key (A1 / A2 / A3 / B1) or the no-person reader: a reader session
# opened for an org with no person bound. It is a first-class reader, not a missing value.
NO_PERSON = "no_person"

TargetKind = Literal["email_message", "email_recipient", "email_attachment"]
Outcome = Literal["allowed", "denied"]

# Target-key prefix -> the table the probe reads. Declared here so a probe literal stays short;
# it is a naming convention of this battery, not an inference about the system.
TARGET_PREFIXES: dict[str, TargetKind] = {
    "M-": "email_message",
    "R-": "email_recipient",
    "ATT-": "email_attachment",
}


@dataclass(frozen=True, slots=True)
class OrgSpec:
    """One synthetic tenant the matrix is loaded into."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    org_key: str
    org_id: UUID
    name: str
    domain: str


@dataclass(frozen=True, slots=True)
class PersonaSpec:
    """One verified person who reads through the real reader plane, person-bound."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    persona_key: str
    org_key: str
    person_id: UUID
    display_name: str
    address: str
    role: Literal["owner_sender", "cc_recipient", "bcc_only", "other_org"]
    has_verified_identity: bool
    must_see_at_least_one_row: bool


@dataclass(frozen=True, slots=True)
class RecipientSpec:
    """One `email_recipient` child row of a message (to / cc / bcc)."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    recipient_key: str
    kind: Literal["to", "cc", "bcc"]
    address: str
    display_name: str | None
    persona_key: str | None


@dataclass(frozen=True, slots=True)
class AttachmentSpec:
    """One `email_attachment` carrier row, with the synthetic bytes it stands for."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    attachment_key: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    is_inline: bool
    extraction_status: str
    extractor_name: str | None
    extractor_version: str | None
    extracted_text: str | None


@dataclass(frozen=True, slots=True)
class GrantSpec:
    """One `acl_grant` row: a persona's retrieval right on the carrying message."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    persona_key: str
    provenance: Literal["recipient", "sender", "owner"]
    revoked: bool


@dataclass(frozen=True, slots=True)
class PromotionSpec:
    """The `visibility_promotion` lineage row a restricted->org widening requires."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    approved_by_user_id: UUID
    from_scope: Literal["restricted"]
    to_scope: Literal["org"]


@dataclass(frozen=True, slots=True)
class MessageSpec:
    """One synthetic `email_message` plus its children, grants and promotion lineage."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    message_key: str
    org_key: str
    state: str
    visibility_scope: Literal["restricted", "org"]
    message_id: str
    from_persona_key: str | None
    from_address: str
    subject: str
    body_text: str
    lexical_marker: str
    language: Literal["bg", "en"]
    recipients: tuple[RecipientSpec, ...]
    attachments: tuple[AttachmentSpec, ...]
    grants: tuple[GrantSpec, ...]
    promotion: PromotionSpec | None


@dataclass(frozen=True, slots=True)
class Probe:
    """One read attempt — (route, state, reader, target) — with its specified outcome.

    `reader_org_key` is the tenant the reader session is opened for and is always explicit: the
    reader plane takes (org_id, person_id) and a no-person reader has no persona to derive an org
    from. A probe whose `reader_org_key` differs from its target's org is a cross-tenant control.
    """

    case_id: str
    criterion_id: str
    origin: str
    expected: Outcome
    route: str
    state: str
    reader: str
    reader_org_key: str
    target_key: str
    target_kind: TargetKind
    lexical_query: str | None = None
    pooled_predecessor: str | None = None


@dataclass(frozen=True, slots=True)
class RouteStateCell:
    """One cell of the route x state matrix and the probes that cover it."""

    case_id: str
    criterion_id: str
    origin: str
    expected: str
    route: str
    state: str
    stage_available: StageLetter
    probe_case_ids: tuple[str, ...]
    allowed_count: int
    denied_count: int


@dataclass(frozen=True, slots=True)
class VisMatrix:
    """The whole VIS battery: what to load, who reads, and what each read must return."""

    version: str
    battery: str
    orgs: tuple[OrgSpec, ...]
    personas: tuple[PersonaSpec, ...]
    messages: tuple[MessageSpec, ...]
    probes: tuple[Probe, ...]
    cells: tuple[RouteStateCell, ...]


# --- the roster: two tenants, four personas ---------------------------------------------------
# Synthetic, obviously-fake UUIDs: the org key and persona key are readable inside the value.
ORG_A_ID = UUID("0a000000-0000-4000-8000-00000000000a")
ORG_B_ID = UUID("0b000000-0000-4000-8000-00000000000b")
PERSON_A1_ID = UUID("00000a01-0000-4000-8000-000000000a01")
PERSON_A2_ID = UUID("00000a02-0000-4000-8000-000000000a02")
PERSON_A3_ID = UUID("00000a03-0000-4000-8000-000000000a03")
PERSON_B1_ID = UUID("00000b01-0000-4000-8000-000000000b01")
APPROVER_A_USER_ID = UUID("00000a99-0000-4000-8000-000000000a99")
APPROVER_B_USER_ID = UUID("00000b99-0000-4000-8000-000000000b99")

ORG_A_DOMAIN = "acme.test"
ORG_B_DOMAIN = "partner.test"
EXTERNAL_DOMAIN = "example.test"

A1_ADDRESS = f"irina.petrova@{ORG_A_DOMAIN}"
A2_ADDRESS = f"nadia.fischer@{ORG_A_DOMAIN}"
A3_ADDRESS = f"yavor.kovachev@{ORG_A_DOMAIN}"
B1_ADDRESS = f"marek.dvorak@{ORG_B_DOMAIN}"
EXTERNAL_SALES = f"sales@{EXTERNAL_DOMAIN}"
EXTERNAL_PROCUREMENT = f"procurement@{EXTERNAL_DOMAIN}"
EXTERNAL_OPS = f"ops@{EXTERNAL_DOMAIN}"

_ORIGIN_TENANT = (
    "PF-01 tenant key: every row carries org_id and the org_isolation RLS policy never lets a "
    "read cross it — two orgs exist so cross-tenant denial has somewhere to fail"
)
_ORIGIN_PERSONA = (
    "criterion sheet VIS / ruling (e): >=3 personas in the main org with non-empty allowed AND "
    "forbidden sets; a persona that sees nothing anywhere invalidates the fixture (deny-all=FAIL)"
)

ORGS: tuple[OrgSpec, ...] = (
    OrgSpec(
        case_id="vis-001",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_ORIGIN_TENANT,
        expected="loaded_as_an_isolated_tenant",
        org_key="A",
        org_id=ORG_A_ID,
        name="Акме Технологии ООД",
        domain=ORG_A_DOMAIN,
    ),
    OrgSpec(
        case_id="vis-002",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_ORIGIN_TENANT,
        expected="loaded_as_an_isolated_tenant",
        org_key="B",
        org_id=ORG_B_ID,
        name="Partner Logistics s.r.o.",
        domain=ORG_B_DOMAIN,
    ),
)

PERSONAS: tuple[PersonaSpec, ...] = (
    PersonaSpec(
        case_id="vis-003",
        criterion_id=CRITERION_NO_MISSING,
        origin=_ORIGIN_PERSONA,
        expected="sees_every_org_A_message_it_holds_a_live_grant_on_plus_org_visible",
        persona_key="A1",
        org_key="A",
        person_id=PERSON_A1_ID,
        display_name="Ирина Петрова",
        address=A1_ADDRESS,
        role="owner_sender",
        has_verified_identity=True,
        must_see_at_least_one_row=True,
    ),
    PersonaSpec(
        case_id="vis-004",
        criterion_id=CRITERION_NO_MISSING,
        origin=_ORIGIN_PERSONA,
        expected="sees_only_the_subset_it_holds_live_grants_on_plus_org_visible",
        persona_key="A2",
        org_key="A",
        person_id=PERSON_A2_ID,
        display_name="Nadia Fischer",
        address=A2_ADDRESS,
        role="cc_recipient",
        has_verified_identity=True,
        must_see_at_least_one_row=True,
    ),
    PersonaSpec(
        case_id="vis-005",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_ORIGIN_PERSONA,
        expected="sees_org_visible_rows_only_no_restricted_row_and_not_its_own_bcc_row",
        persona_key="A3",
        org_key="A",
        person_id=PERSON_A3_ID,
        display_name="Явор Ковачев",
        address=A3_ADDRESS,
        role="bcc_only",
        has_verified_identity=True,
        must_see_at_least_one_row=True,
    ),
    PersonaSpec(
        case_id="vis-006",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_ORIGIN_PERSONA,
        expected="sees_only_org_B_rows_and_never_any_org_A_row",
        persona_key="B1",
        org_key="B",
        person_id=PERSON_B1_ID,
        display_name="Marek Dvorak",
        address=B1_ADDRESS,
        role="other_org",
        has_verified_identity=True,
        must_see_at_least_one_row=True,
    ),
)
