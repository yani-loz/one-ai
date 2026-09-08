"""IDENT alias battery, part 2 (public F evidence, PII-free).

Role: carries ``ALIAS_PAIRS_C`` — the second contiguous run of confirmed aliases with provenance
    that MUST resolve to one identity. It is the continuation of ``ALIAS_PAIRS_A`` in
    ``ident_cases_a``; ``ident_cases`` concatenates the two into the public ``ALIAS_PAIRS``.
Used by: ``tools.mem01_verify.fixtures.ident_cases`` (the public re-export named in contract §1.3)
    and ``fixtures.digest.fixtures_digest`` (the battery digest enters ``config_hash``).
Depends on: ``tools.mem01_verify.fixtures.ident_cases_a`` for the record types and the
    ``ALIAS_RESOLUTION`` criterion id — data only.
Key invariants:
    - The split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries no
      semantics. ``ALIAS_PAIRS_C`` is a contiguous SUFFIX of the alias battery:
      ``ALIAS_PAIRS_A + ALIAS_PAIRS_C`` reproduces the authored order exactly.
    - The battery's full invariant set — R12 independence, the four leading provenance fields,
      PII-free reserved ``.test`` addresses, disjointness from ``DISTINCT_PAIRS`` and
      ``STABILITY_CONTROLS``, and the five confirming ``source_kind`` values — is stated once, in
      ``ident_cases_a``, and governs the records here.
    - No gate imports ``ALIAS_PAIRS_C``; gates import ``ALIAS_PAIRS`` from ``ident_cases``.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.ident_cases_a import (
    ALIAS_RESOLUTION,
    AliasPair,
    AliasProvenance,
)

ALIAS_PAIRS_C: tuple[AliasPair, ...] = (
    AliasPair(
        case_id="ident-014",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="one side carries no display name at all; confirmation still licenses the merge",
        org_key="org_a",
        language="bg",
        address_a="viktoriya.slavova@acme.test",
        address_b="v.slavova@example.test",
        display_name_a="Виктория Славова",
        display_name_b="",
        provenance=AliasProvenance(
            "owner_confirmation",
            "owner@acme.test",
            "2026-05-14T16:35:00Z",
            "Собственикът потвърди псевдонима.",
        ),
    ),
    AliasPair(
        case_id="ident-015",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="initial-form mailbox in the SECOND tenant — alias resolution is per tenant",
        org_key="org_b",
        language="en",
        address_a="rmiller@partner.test",
        address_b="rachel.miller@partner.test",
        display_name_a="Rachel Miller",
        display_name_b="Rachel Miller",
        provenance=AliasProvenance(
            "directory_record",
            "hr@partner.test",
            "2026-02-25T08:30:00Z",
            "Directory: one mailbox, two forms.",
        ),
    ),
    AliasPair(
        case_id="ident-016",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="a Reply-To mailbox declared as the sender's own second address",
        org_key="org_a",
        language="bg",
        address_a="stefan.kolev@acme.test",
        address_b="s.kolev@partner.test",
        display_name_a="Стефан Колев",
        display_name_b="Стефан Колев",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "stefan.kolev@acme.test",
            "2026-06-21T15:10:00Z",
            "Отговаряйте на s.kolev — мой адрес.",
        ),
    ),
    AliasPair(
        case_id="ident-017",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="identical local part on two org domains WITH owner confirmation (contrast: "
        "ident-111..116, where the same shape without confirmation stays distinct)",
        org_key="org_a",
        language="en",
        address_a="peter.novak@acme.test",
        address_b="peter.novak@partner.test",
        display_name_a="Peter Novak",
        display_name_b="Peter Novak",
        provenance=AliasProvenance(
            "owner_confirmation",
            "owner@acme.test",
            "2026-05-14T16:40:00Z",
            "Owner confirmed the same person.",
        ),
    ),
    AliasPair(
        case_id="ident-018",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="authoritative alias table entry for a first-initial mailbox",
        org_key="org_a",
        language="bg",
        address_a="ralitsa.marinova@acme.test",
        address_b="r.marinova@acme.test",
        display_name_a="Ралица Маринова",
        display_name_b="Ралица Маринова",
        provenance=AliasProvenance(
            "mail_system_alias_record",
            "mailadmin@acme.test",
            "2026-02-01T07:10:00Z",
            "alias: r.marinova -> ralitsa.marinova",
        ),
    ),
    AliasPair(
        case_id="ident-019",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="second tenant, personal mailbox declared in-thread by the person",
        org_key="org_b",
        language="en",
        address_a="laura.bennett@partner.test",
        address_b="laura.bennett.home@example.test",
        display_name_a="Laura Bennett",
        display_name_b="Laura Bennett",
        provenance=AliasProvenance(
            "explicit_confirmation",
            "laura.bennett@partner.test",
            "2026-03-30T18:20:00Z",
            "My home address, same person.",
        ),
    ),
    AliasPair(
        case_id="ident-020",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="Cyrillic vs Latin display name for one confirmed person (bilingual corpus shape)",
        org_key="org_a",
        language="bg",
        address_a="nikola.stanev@acme.test",
        address_b="n.stanev@example.test",
        display_name_a="Никола Станев",
        display_name_b="Nikola Stanev",
        provenance=AliasProvenance(
            "directory_record",
            "hr@acme.test",
            "2026-01-20T08:15:00Z",
            "Личният адрес е записан в досието на служителя.",
        ),
    ),
    AliasPair(
        case_id="ident-021",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="dot AND plus differences at once, licensed only by the ownership proof",
        org_key="org_a",
        language="en",
        address_a="oliver.wright@acme.test",
        address_b="o.wright+news@example.test",
        display_name_a="Oliver Wright",
        display_name_b="Oliver Wright",
        provenance=AliasProvenance(
            "mailbox_ownership_proof",
            "o.wright+news@example.test",
            "2026-04-04T12:15:00Z",
            "Challenge token answered from B.",
        ),
    ),
    AliasPair(
        case_id="ident-022",
        criterion_id=ALIAS_RESOLUTION,
        expected="same_identity",
        origin="consulting mailbox at the partner domain, confirmed by the owner",
        org_key="org_a",
        language="bg",
        address_a="kalina.petkova@acme.test",
        address_b="k.petkova.consult@partner.test",
        display_name_a="Калина Петкова",
        display_name_b="Калина Петкова",
        provenance=AliasProvenance(
            "owner_confirmation",
            "owner@acme.test",
            "2026-05-14T16:45:00Z",
            "Потвърдено: същият човек.",
        ),
    ),
)
