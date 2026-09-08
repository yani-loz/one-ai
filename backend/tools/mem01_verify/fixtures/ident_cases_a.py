"""IDENT fixture record types and the alias battery, part 1 (public F evidence, PII-free).

Role: defines the three frozen record types of the IDENT battery and the three F criterion ids,
    and carries ``ALIAS_PAIRS_A`` — the first contiguous run of confirmed aliases with provenance
    that MUST resolve to one identity. The rest of the alias runs live in ``ident_cases_c``, the
    stability controls in ``ident_cases_d``, and the must-remain-distinct pairs in
    ``ident_cases_b`` / ``ident_cases_e``; ``ident_cases`` assembles the public tuples.
Used by: ``tools.mem01_verify.fixtures.ident_cases`` (the public re-export named in contract §1.3),
    ``ident_cases_c`` and ``ident_cases_d`` (record types and criterion ids), ``ident_cases_b``
    and ``ident_cases_e`` (the ``NO_FALSE_MERGE`` criterion id), and
    ``fixtures.digest.fixtures_digest`` (the battery digest enters ``config_hash``).
Depends on: nothing inside the project — data only (stdlib ``dataclasses`` / ``typing``).
Key invariants:
    - The A/C/D split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries
      no semantics. ``ALIAS_PAIRS_A`` is a contiguous PREFIX of the alias battery:
      ``ALIAS_PAIRS_A + ALIAS_PAIRS_C`` reproduces the authored order exactly, so the battery
      digest does not move because a record changed file.
    - Expectations are authored independently of every measured component (contract R12): they come
      from criterion 13 (`ident.provisional.*`), RFC 5321/5322 and the synthetic originals below,
      NEVER from running the address resolver or a normalization key builder.
    - Every record carries ``case_id`` (unique across the whole battery), ``criterion_id``,
      ``origin`` and ``expected`` as its first four fields, in that order.
    - Synthetic and PII-free: every mailbox is under the RFC 6761 reserved TLD ``.test``; names are
      invented; no corpus text is reproduced.
    - Address strings are DISJOINT between ``ALIAS_PAIRS``, ``DISTINCT_PAIRS`` and
      ``STABILITY_CONTROLS`` — no address may carry two contradictory expectations when the whole
      battery is arranged on one probe database.
    - Confirmation is authoritative or it is not confirmation: only the five ``source_kind`` values
      below license a merge. Weak signals (a shared signature block, an identical display name, a
      CRM contact alias) belong to the must-remain-distinct tuple, never here.
    - Contract §11 predicts ``ALIAS_PAIRS`` FAILS in Stage A (no alias registry exists yet). That is
      the intended baseline, not a defect in these fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ALIAS_RESOLUTION = "ident.provisional.alias_resolution"
NO_FALSE_MERGE = "ident.provisional.no_false_merge"
EXACT_ADDRESS_STABILITY = "ident.provisional.exact_address_stability"

AliasSourceKind = Literal[
    "explicit_confirmation",
    "directory_record",
    "owner_confirmation",
    "mailbox_ownership_proof",
    "mail_system_alias_record",
]


@dataclass(frozen=True, slots=True)
class AliasProvenance:
    """The authoritative record that confirms two mailboxes belong to one person.

    Contract: a merge is licensed ONLY by one of the five authoritative ``source_kind`` values,
    each carrying who confirmed it, when, and the synthetic evidence text.
    """

    source_kind: AliasSourceKind
    confirmed_by: str
    confirmed_at: str
    evidence_text: str


@dataclass(frozen=True, slots=True)
class AliasPair:
    """Two addresses a confirmed alias binds to one tenant-scoped identity."""

    case_id: str
    criterion_id: str
    origin: str
    expected: Literal["same_identity"]
    org_key: str
    language: Literal["bg", "en"]
    address_a: str
    address_b: str
    display_name_a: str
    display_name_b: str
    provenance: AliasProvenance


@dataclass(frozen=True, slots=True)
class StabilityControl:
    """One exact address observed twice; both observations must yield one stable identity."""

    case_id: str
    criterion_id: str
    origin: str
    expected: Literal["stable"]
    org_key: str
    language: Literal["bg", "en"]
    address: str
    first_seen_header: Literal["From", "To", "Cc", "Reply-To"]
    first_seen_raw: str
    second_seen_header: Literal["From", "To", "Cc", "Reply-To"]
    second_seen_raw: str


ALIAS_PAIRS_A: tuple[AliasPair, ...] = (
    AliasPair(
        case_id="ident-001",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="work + personal mailbox declared in-thread by the person; criterion 13 example",
        org_key="org_a",
        language="bg",
        address_a="ivan.petrov@acme.test",
        address_b="ivan.petrov.lichen@example.test",
        display_name_a="Иван Петров",
        display_name_b="Иван Петров",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "ivan.petrov@acme.test",
            "2026-03-04T09:12:00Z",
            "Пиша от личния си адрес — аз съм.",
        ),
    ),
    AliasPair(
        case_id="ident-002",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="same local part on two domains, licensed by an explicit in-thread confirmation",
        org_key="org_a",
        language="en",
        address_a="emma.clarke@acme.test",
        address_b="emma.clarke@example.test",
        display_name_a="Emma Clarke",
        display_name_b="Emma Clarke",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "emma.clarke@acme.test",
            "2026-02-11T14:40:00Z",
            "Writing from my private mailbox.",
        ),
    ),
    AliasPair(
        case_id="ident-003",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="surname change recorded in the org directory — one person, two mailboxes",
        org_key="org_a",
        language="bg",
        address_a="mariya.georgieva@acme.test",
        address_b="mariya.todorova@acme.test",
        display_name_a="Мария Георгиева",
        display_name_b="Мария Тодорова",
        provenance=AliasProvenance(
            "directory_record",
            "hr@acme.test",
            "2026-01-20T08:00:00Z",
            "Смяна на фамилия; предишният адрес остава псевдоним.",
        ),
    ),
    AliasPair(
        case_id="ident-004",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="short-form and long-form mailbox of one employee, per the directory record",
        org_key="org_a",
        language="bg",
        address_a="g.dimitrov@acme.test",
        address_b="georgi.dimitrov@acme.test",
        display_name_a="Георги Димитров",
        display_name_b="Георги Димитров",
        provenance=AliasProvenance(
            "directory_record",
            "it@acme.test",
            "2026-01-20T08:05:00Z",
            "Двата адреса сочат към една пощенска кутия.",
        ),
    ),
    AliasPair(
        case_id="ident-005",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="plus-tagged address CONFIRMED by ownership proof — the licence is the proof, "
        "never the plus rule (contrast: ident-106..110 stay distinct)",
        org_key="org_a",
        language="en",
        address_a="sarah.jones@acme.test",
        address_b="sarah.jones+billing@example.test",
        display_name_a="Sarah Jones",
        display_name_b="Sarah Jones",
        provenance=AliasProvenance(
            "mailbox_ownership_proof",
            "sarah.jones+billing@example.test",
            "2026-04-02T11:05:00Z",
            "Challenge token answered from B.",
        ),
    ),
    AliasPair(
        case_id="ident-006",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="dot variant CONFIRMED by ownership proof (contrast: ident-100..105 stay distinct)",
        org_key="org_a",
        language="bg",
        address_a="elena.stoyanova@example.test",
        address_b="elenastoyanova@example.test",
        display_name_a="Елена Стоянова",
        display_name_b="Елена Стоянова",
        provenance=AliasProvenance(
            "mailbox_ownership_proof",
            "elenastoyanova@example.test",
            "2026-04-02T11:20:00Z",
            "Отговор на контролен код от B.",
        ),
    ),
    AliasPair(
        case_id="ident-007",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="contractor's partner-side mailbox confirmed by the org owner in the review queue",
        org_key="org_a",
        language="en",
        address_a="michael.brown@acme.test",
        address_b="m.brown@partner.test",
        display_name_a="Michael Brown",
        display_name_b="Michael Brown",
        provenance=AliasProvenance(
            "owner_confirmation",
            "owner@acme.test",
            "2026-05-14T16:30:00Z",
            "Owner confirmed: same contractor.",
        ),
    ),
    AliasPair(
        case_id="ident-008",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="legacy numeric mailbox mapped to the named one by the mail system's alias table",
        org_key="org_a",
        language="bg",
        address_a="u10427@acme.test",
        address_b="nikolay.todorov@acme.test",
        display_name_a="",
        display_name_b="Николай Тодоров",
        provenance=AliasProvenance(
            "mail_system_alias_record",
            "mailadmin@acme.test",
            "2026-02-01T07:00:00Z",
            "alias: u10427 -> nikolay.todorov",
        ),
    ),
    AliasPair(
        case_id="ident-009",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="second mailbox on a mail subdomain, declared by the person in-thread",
        org_key="org_a",
        language="bg",
        address_a="daniela.ivanova@acme.test",
        address_b="d.ivanova@mail.acme.test",
        display_name_a="Даниела Иванова",
        display_name_b="Даниела Иванова",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "daniela.ivanova@acme.test",
            "2026-03-19T10:02:00Z",
            "Вторият ми адрес е d.ivanova.",
        ),
    ),
    AliasPair(
        case_id="ident-010",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="mail-server migration to a subdomain, authoritative alias export",
        org_key="org_a",
        language="en",
        address_a="john.smith@acme.test",
        address_b="john.smith@mail.acme.test",
        display_name_a="John Smith",
        display_name_b="John Smith",
        provenance=AliasProvenance(
            "mail_system_alias_record",
            "mailadmin@acme.test",
            "2026-02-01T07:05:00Z",
            "alias: acme.test -> mail.acme.test",
        ),
    ),
    AliasPair(
        case_id="ident-011",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="two transliterations of one Bulgarian given name, joined by the directory record",
        org_key="org_a",
        language="bg",
        address_a="petar.angelov@acme.test",
        address_b="petur.angelov@acme.test",
        display_name_a="Петър Ангелов",
        display_name_b="Петър Ангелов",
        provenance=AliasProvenance(
            "directory_record",
            "hr@acme.test",
            "2026-01-20T08:10:00Z",
            "Двете изписвания са на един служител.",
        ),
    ),
    AliasPair(
        case_id="ident-012",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="bilingual display names on one person; the licence is the in-thread confirmation",
        org_key="org_a",
        language="bg",
        address_a="yordan.iliev@acme.test",
        address_b="jordan.iliev@example.test",
        display_name_a="Йордан Илиев",
        display_name_b="Jordan Iliev",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "yordan.iliev@acme.test",
            "2026-06-08T13:44:00Z",
            "Личната ми поща е jordan.iliev.",
        ),
    ),
    AliasPair(
        case_id="ident-013",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="hyphen sub-addressing confirmed by ownership proof, not by a hyphen rule",
        org_key="org_a",
        language="en",
        address_a="david.hall-invoices@example.test",
        address_b="david.hall@example.test",
        display_name_a="David Hall",
        display_name_b="David Hall",
        provenance=AliasProvenance(
            "mailbox_ownership_proof",
            "david.hall@example.test",
            "2026-04-03T09:00:00Z",
            "Challenge token answered from A.",
        ),
    ),
)
