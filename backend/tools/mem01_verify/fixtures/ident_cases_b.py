"""IDENT must-remain-distinct battery, part 1 (public F evidence, PII-free).

Role: defines the ``DistinctPair`` record type and its category vocabulary, and carries
    ``DISTINCT_PAIRS_B`` — the first contiguous run of address pairs that a resolver MUST keep as
    separate identities because no confirmed alias with provenance exists for them. It is the
    no-false-merge half of criterion 13; the remaining pairs live in ``ident_cases_e``,
    ``ident_cases_a`` / ``ident_cases_c`` carry the merge half and the record types, and
    ``ident_cases_d`` the stability controls.
Used by: ``tools.mem01_verify.fixtures.ident_cases`` (the public re-export named in contract §1.3),
    ``ident_cases_e`` (record type and category vocabulary), and
    ``fixtures.digest.fixtures_digest``.
Depends on: ``tools.mem01_verify.fixtures.ident_cases_a`` for ``NO_FALSE_MERGE`` only — data only.
Key invariants:
    - The B/E split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries no
      semantics. ``DISTINCT_PAIRS_B`` is a contiguous PREFIX of the battery:
      ``DISTINCT_PAIRS_B + DISTINCT_PAIRS_E`` reproduces the authored order exactly, so the
      battery digest does not move because a record changed file.
    - Expectations come from criterion 13 ("никакво сливане по име, blanket Gmail dot/plus collapse
      или промяна на contact alias, която тихо слива access principals") and from RFC 5321/5322,
      never from running the resolver (contract R12).
    - Every address here is DISJOINT from ``ALIAS_PAIRS`` and ``STABILITY_CONTROLS``: the same
      string never carries both a merge and a no-merge expectation.
    - Look-alike and IDN cases are homoglyphs of the reserved fixture domains; they stay under the
      RFC 6761 reserved ``.test`` TLD so no fixture address can ever route anywhere. Homoglyph code
      points are written as ``\\uXXXX`` escapes so a reviewer can see the substitution; the A-label
      each one encodes to is stated in ``origin``.
    - Identical display names, shared signature blocks and CRM contact aliases are WEAK signals:
      they appear here, as things that must not merge, and never in ``ALIAS_PAIRS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tools.mem01_verify.fixtures.ident_cases_a import NO_FALSE_MERGE

DistinctCategory = Literal[
    "dot_variant",
    "plus_variant",
    "same_display_name",
    "role_address",
    "shared_mailbox",
    "lookalike_domain",
    "idn_lookalike",
    "cross_tenant",
    "weak_signal",
]


@dataclass(frozen=True, slots=True)
class DistinctPair:
    """Two addresses that must resolve to two identities (no confirmed alias exists)."""

    case_id: str
    criterion_id: str
    origin: str
    expected: Literal["distinct"]
    category: DistinctCategory
    org_key_a: str
    org_key_b: str
    language: Literal["bg", "en"]
    address_a: str
    address_b: str
    display_name_a: str
    display_name_b: str


DISTINCT_PAIRS_B: tuple[DistinctPair, ...] = (
    DistinctPair(
        case_id="ident-100",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="dots removed: a provider-specific convenience, never an identity rule",
        category="dot_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="boris.angelov@example.test",
        address_b="borisangelov@example.test",
        display_name_a="Борис Ангелов",
        display_name_b="Борис Ангелов",
    ),
    DistinctPair(
        case_id="ident-101",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="an extra dot inside the local part; RFC 5321 leaves the local part opaque",
        category="dot_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="t.dimitrova@example.test",
        address_b="t.di.mitrova@example.test",
        display_name_a="Теодора Димитрова",
        display_name_b="Теодора Димитрова",
    ),
    DistinctPair(
        case_id="ident-102",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="dotless variant of an English mailbox, no confirmation on record",
        category="dot_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="en",
        address_a="james.cole@example.test",
        address_b="jamescole@example.test",
        display_name_a="James Cole",
        display_name_b="James Cole",
    ),
    DistinctPair(
        case_id="ident-103",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="initials written with and without an inner dot",
        category="dot_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="a.n.georgiev@example.test",
        address_b="an.georgiev@example.test",
        display_name_a="Александър Георгиев",
        display_name_b="Ангел Георгиев",
    ),
    DistinctPair(
        case_id="ident-104",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="dot moved inside the given name — a different mailbox, not a spelling of one",
        category="dot_variant",
        org_key_a="org_b",
        org_key_b="org_b",
        language="en",
        address_a="lucy.ward@example.test",
        address_b="l.ucy.ward@example.test",
        display_name_a="Lucy Ward",
        display_name_b="Lucy Ward",
    ),
    DistinctPair(
        case_id="ident-105",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="dot inserted into the surname of a Bulgarian mailbox",
        category="dot_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="milena.rasheva@example.test",
        address_b="milena.ra.sheva@example.test",
        display_name_a="Милена Рашева",
        display_name_b="Милена Рашева",
    ),
    DistinctPair(
        case_id="ident-106",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="base address vs a plus-tagged one with no ownership proof",
        category="plus_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="hristo.vasilev@example.test",
        address_b="hristo.vasilev+bg@example.test",
        display_name_a="Христо Василев",
        display_name_b="Христо Василев",
    ),
    DistinctPair(
        case_id="ident-107",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="sub-addressing is a provider feature; only a proof licenses the merge",
        category="plus_variant",
        org_key_a="org_b",
        org_key_b="org_b",
        language="en",
        address_a="anna.foster@example.test",
        address_b="anna.foster+jobs@example.test",
        display_name_a="Anna Foster",
        display_name_b="Anna Foster",
    ),
    DistinctPair(
        case_id="ident-108",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="two different tags on one apparent base — still two unconfirmed mailboxes",
        category="plus_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="k.marinov+2026@example.test",
        address_b="k.marinov+2025@example.test",
        display_name_a="Красимир Маринов",
        display_name_b="Красимир Маринов",
    ),
    DistinctPair(
        case_id="ident-109",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="two tags, English side; a blanket plus-collapse would merge them wrongly",
        category="plus_variant",
        org_key_a="org_b",
        org_key_b="org_b",
        language="en",
        address_a="tom.reed+alpha@example.test",
        address_b="tom.reed+beta@example.test",
        display_name_a="Tom Reed",
        display_name_b="Tom Reed",
    ),
    DistinctPair(
        case_id="ident-110",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="mailing-list tag on an otherwise identical local part, unconfirmed",
        category="plus_variant",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="zlatina.koleva@example.test",
        address_b="zlatina.koleva+lists@example.test",
        display_name_a="Златина Колева",
        display_name_b="Златина Колева",
    ),
    DistinctPair(
        case_id="ident-111",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="criterion 13 worked example: two unrelated 'Иван Петров' on two domains",
        category="same_display_name",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="ipetrov@acme.test",
        address_b="ipetrov@partner.test",
        display_name_a="Иван Петров",
        display_name_b="Иван Петров",
    ),
    DistinctPair(
        case_id="ident-112",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="a very common Bulgarian name on the customer and the supplier domain",
        category="same_display_name",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="mariya.ivanova@acme.test",
        address_b="mariya.ivanova@partner.test",
        display_name_a="Мария Иванова",
        display_name_b="Мария Иванова",
    ),
    DistinctPair(
        case_id="ident-113",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="the English equivalent: two 'John Smith' with no confirmation between them",
        category="same_display_name",
        org_key_a="org_a",
        org_key_b="org_a",
        language="en",
        address_a="jsmith@acme.test",
        address_b="jsmith@partner.test",
        display_name_a="John Smith",
        display_name_b="John Smith",
    ),
    DistinctPair(
        case_id="ident-114",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="employee and an unrelated external person sharing a name",
        category="same_display_name",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="ggeorgiev@acme.test",
        address_b="ggeorgiev@example.test",
        display_name_a="Георги Георгиев",
        display_name_b="Георги Георгиев",
    ),
    DistinctPair(
        case_id="ident-115",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="same name at a partner and at a consumer provider, unconfirmed",
        category="same_display_name",
        org_key_a="org_b",
        org_key_b="org_b",
        language="en",
        address_a="anovak@partner.test",
        address_b="anovak@example.test",
        display_name_a="Anna Novak",
        display_name_b="Anna Novak",
    ),
    DistinctPair(
        case_id="ident-116",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="name match plus a near-identical local part is still not a confirmed alias",
        category="same_display_name",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="ddimitrov@acme.test",
        address_b="d.dimitrov@partner.test",
        display_name_a="Димитър Димитров",
        display_name_b="Димитър Димитров",
    ),
    DistinctPair(
        case_id="ident-117",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="the same role local part at two organisations — two role identities",
        category="role_address",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="info@acme.test",
        address_b="info@partner.test",
        display_name_a="Акме ООД",
        display_name_b="Партнер ЕООД",
    ),
    DistinctPair(
        case_id="ident-118",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="a role mailbox is never the person who happens to answer from it",
        category="role_address",
        org_key_a="org_a",
        org_key_b="org_a",
        language="bg",
        address_a="office@acme.test",
        address_b="elena.dimova@acme.test",
        display_name_a="Офис Акме",
        display_name_b="Елена Димова",
    ),
    DistinctPair(
        case_id="ident-119",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="one role name on the org domain and on a consumer provider",
        category="role_address",
        org_key_a="org_a",
        org_key_b="org_a",
        language="en",
        address_a="sales@acme.test",
        address_b="sales@example.test",
        display_name_a="Acme Sales",
        display_name_b="Sales",
    ),
    DistinctPair(
        case_id="ident-120",
        criterion_id=NO_FALSE_MERGE,
        expected="distinct",
        origin="HR role mailboxes of two organisations must not collapse into one principal",
        category="role_address",
        org_key_a="org_a",
        org_key_b="org_a",
        language="en",
        address_a="hr@acme.test",
        address_b="hr@partner.test",
        display_name_a="Acme HR",
        display_name_b="Partner HR",
    ),
)
