"""Fixture record SHAPES for the deferred NF (deduplication) and LANG gates.

Role:
    Declares the frozen dataclass schemas the stage-C batteries for noise filtering /
    deduplication (NF) and language classification (LANG) will be authored against, plus a
    handful of SMOKE records that prove each schema is constructible and carries a real,
    independently specified expectation. Contract 10.9: in Stage A these gates ship their schema
    and print `incomplete`.
Used by:
    `tools.mem01_verify.fixtures.stubs` (re-export facade), the release schema emitter, and the
    NF / LANG gate evaluators once their batteries exist.
Depends on:
    `tools.mem01_verify.fixtures.stubs_a` (`FixtureRecord`). No database, no measured component,
    no third-party package.
Key invariants:
    - Every record is a frozen dataclass carrying `case_id`, `criterion_id`, `origin`,
      `expected` (contract 10) and `smoke`.
    - `smoke=True` marks a schema-proof record: it never enters a numerator, a denominator, or a
      criterion `minimum`.
    - `criterion_id` is always an id that exists in `release/criteria.step1.v1.yaml` for the
      record's own gate.
    - Expectations come from the criterion sheet and the founder defaults, never from running a
      measured component (contract R12).
    - PII-free and synthetic: addresses only under `example.test`, `acme.test`, `partner.test`;
      no real corpus text; Bulgarian and English cases both present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .stubs_a import FixtureRecord

# --------------------------------------------------------------------------------------- NF ---


@dataclass(frozen=True, kw_only=True)
class DedupMember:
    """One synthetic attachment participating in a deduplication audit group."""

    member_id: str
    org_label: Literal["A", "B"]
    content: bytes
    filename: str
    content_type: str
    is_inline: bool


@dataclass(frozen=True, kw_only=True)
class DedupGroupExpectation:
    """The correct canonical/carrier grouping for one audited dedup scenario.

    Attributes:
        canonical_member_id: the member that must be elected canonical.
        carrier_member_ids: every member that must carry that canonical (sorted, includes it).
        must_not_merge_member_ids: members that must stay outside the group — a different
            tenant (dedup is within-tenant only) or merely similar content.
        group_count: how many distinct canonical groups the scenario must produce in total.
    """

    canonical_member_id: str
    carrier_member_ids: tuple[str, ...]
    must_not_merge_member_ids: tuple[str, ...]
    group_count: int


@dataclass(frozen=True, kw_only=True)
class DedupGroupCase(FixtureRecord[DedupGroupExpectation]):
    """One deduplication audit scenario over synthetic attachment bytes."""

    members: tuple[DedupMember, ...]


_NF_CONTRACT_BYTES = "Договор за доставка №2025-114\nСрок: 10 работни дни\n".encode()
_NF_OTHER_BYTES = "Договор за доставка №2025-115\nСрок: 20 работни дни\n".encode()


NF_SMOKE_CASES: tuple[DedupGroupCase, ...] = (
    DedupGroupCase(
        case_id="nf-001",
        criterion_id="nf.dedup_groups_correct",
        origin=(
            "NF criterion 3: exact content identity deduplicates WITHIN a tenant only; the "
            "byte-identical copy in org B is a separate canonical, never a carrier of org A's."
        ),
        smoke=True,
        members=(
            DedupMember(
                member_id="nf-a-m1",
                org_label="A",
                content=_NF_CONTRACT_BYTES,
                filename="dogovor.pdf",
                content_type="application/pdf",
                is_inline=False,
            ),
            DedupMember(
                member_id="nf-a-m2",
                org_label="A",
                content=_NF_CONTRACT_BYTES,
                filename="dogovor-kopie.pdf",
                content_type="application/pdf",
                is_inline=False,
            ),
            DedupMember(
                member_id="nf-b-m1",
                org_label="B",
                content=_NF_CONTRACT_BYTES,
                filename="dogovor.pdf",
                content_type="application/pdf",
                is_inline=False,
            ),
        ),
        expected=DedupGroupExpectation(
            canonical_member_id="nf-a-m1",
            carrier_member_ids=("nf-a-m1", "nf-a-m2"),
            must_not_merge_member_ids=("nf-b-m1",),
            group_count=2,
        ),
    ),
    DedupGroupCase(
        case_id="nf-002",
        criterion_id="nf.dedup_groups_correct",
        origin=(
            "NF criterion 3: merely similar files never merge — same filename and same tenant "
            "but one changed clause is two canonicals, not one canonical with a carrier."
        ),
        smoke=True,
        members=(
            DedupMember(
                member_id="nf-a-m3",
                org_label="A",
                content=_NF_CONTRACT_BYTES,
                filename="dogovor.pdf",
                content_type="application/pdf",
                is_inline=False,
            ),
            DedupMember(
                member_id="nf-a-m4",
                org_label="A",
                content=_NF_OTHER_BYTES,
                filename="dogovor.pdf",
                content_type="application/pdf",
                is_inline=False,
            ),
        ),
        expected=DedupGroupExpectation(
            canonical_member_id="nf-a-m3",
            carrier_member_ids=("nf-a-m3",),
            must_not_merge_member_ids=("nf-a-m4",),
            group_count=2,
        ),
    ),
)


# ------------------------------------------------------------------------------------- LANG ---


@dataclass(frozen=True, kw_only=True)
class LanguageExpectation:
    """The correct language class for one item, with the token evidence that decides `mixed`.

    Attributes:
        label: the gold class, restricted to the four legal states.
        assessable: whether the item carries enough prose to be scored at all (an
            unassessable item may legitimately be `und`; an assessable one may not).
        bg_word_tokens: Bulgarian language-bearing word tokens counted in the original.
        en_word_tokens: English language-bearing word tokens counted in the original.
    """

    label: Literal["bg", "en", "mixed", "und"]
    assessable: bool
    bg_word_tokens: int
    en_word_tokens: int


@dataclass(frozen=True, kw_only=True)
class LanguageCase(FixtureRecord[LanguageExpectation]):
    """One quote/signature-stripped prose item with its gold language class."""

    item_kind: Literal["email_body", "email_subject", "attachment_text"]
    prose_text: str


LANG_SMOKE_CASES: tuple[LanguageCase, ...] = (
    LanguageCase(
        case_id="lang-001",
        criterion_id="lang.accuracy_per_class",
        origin="LANG criterion 4: monolingual Bulgarian prose is class `bg`, never `und`.",
        smoke=True,
        item_kind="email_body",
        prose_text="Приложено изпращаме подписания договор за доставка на офис оборудване.",
        expected=LanguageExpectation(
            label="bg", assessable=True, bg_word_tokens=9, en_word_tokens=0
        ),
    ),
    LanguageCase(
        case_id="lang-002",
        criterion_id="lang.accuracy_per_class",
        origin="LANG criterion 4: monolingual English prose is class `en`, never `und`.",
        smoke=True,
        item_kind="email_body",
        prose_text="Please find attached the signed supply agreement for the office equipment.",
        expected=LanguageExpectation(
            label="en", assessable=True, bg_word_tokens=0, en_word_tokens=11
        ),
    ),
    LanguageCase(
        case_id="lang-003",
        criterion_id="lang.accuracy_per_class",
        origin=(
            "Founder default `mixed_language`: each language holds >= 20% of the language-bearing "
            "word tokens with >= 5 tokens each (6 bg / 8 en of 14), so the class is `mixed`."
        ),
        smoke=True,
        item_kind="email_body",
        prose_text=(
            "Приложено изпращаме подписания договор за доставка. "
            "Please countersign and return the scanned copy today."
        ),
        expected=LanguageExpectation(
            label="mixed", assessable=True, bg_word_tokens=6, en_word_tokens=8
        ),
    ),
)
