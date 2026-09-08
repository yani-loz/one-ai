"""IDENT exact-address stability battery (public F evidence, PII-free).

Role: carries ``STABILITY_CONTROLS`` — one exact address observed twice, which MUST map to the
    same tenant-scoped identity. It is the third of the three IDENT tuples; the alias pairs live
    in ``ident_cases_a`` / ``ident_cases_c`` and the must-remain-distinct pairs in
    ``ident_cases_b`` / ``ident_cases_e``.
Used by: ``tools.mem01_verify.fixtures.ident_cases`` (the public re-export named in contract §1.3),
    the IDENT gate evaluator, and ``fixtures.digest.fixtures_digest``.
Depends on: ``tools.mem01_verify.fixtures.ident_cases_a`` for the ``StabilityControl`` record type
    and the ``EXACT_ADDRESS_STABILITY`` criterion id — data only.
Key invariants:
    - This module exists as a separate file only to keep each IDENT module under the file-size
      ceiling of `.claude/rules/code-quality.md` A2; the tuple's contents and order are unchanged
      by the move, so the battery digest is unchanged by it.
    - Expectations are authored independently of every measured component (contract R12): a
      stability control asserts what an exact-match resolver MUST do, never what one was observed
      doing.
    - Every record carries ``case_id`` (unique across the whole battery), ``criterion_id``,
      ``origin`` and ``expected`` as its first four fields, in that order; ids are ``ident-2NN``.
    - Synthetic and PII-free: every mailbox is under the RFC 6761 reserved TLD ``.test``.
    - Addresses here are DISJOINT from ``ALIAS_PAIRS`` and ``DISTINCT_PAIRS``.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.ident_cases_a import (
    EXACT_ADDRESS_STABILITY,
    StabilityControl,
)

STABILITY_CONTROLS: tuple[StabilityControl, ...] = (
    StabilityControl(
        case_id="ident-200",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="byte-identical observation twice — the minimal stability control",
        org_key="org_a",
        language="bg",
        address="radost.mihaylova@acme.test",
        first_seen_header="From",
        first_seen_raw="Радост Михайлова <radost.mihaylova@acme.test>",
        second_seen_header="From",
        second_seen_raw="Радост Михайлова <radost.mihaylova@acme.test>",
    ),
    StabilityControl(
        case_id="ident-201",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="byte-identical observation twice, English side of the bilingual corpus",
        org_key="org_b",
        language="en",
        address="adam.price@partner.test",
        first_seen_header="To",
        first_seen_raw="Adam Price <adam.price@partner.test>",
        second_seen_header="To",
        second_seen_raw="Adam Price <adam.price@partner.test>",
    ),
    StabilityControl(
        case_id="ident-202",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="display name present, then absent — the mailbox is the identity, not the label",
        org_key="org_a",
        language="bg",
        address="todor.todorov@acme.test",
        first_seen_header="From",
        first_seen_raw="Тодор Тодоров <todor.todorov@acme.test>",
        second_seen_header="From",
        second_seen_raw="<todor.todorov@acme.test>",
    ),
    StabilityControl(
        case_id="ident-203",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="Cyrillic then transliterated display name on one exact address",
        org_key="org_a",
        language="bg",
        address="slavka.petkova@acme.test",
        first_seen_header="From",
        first_seen_raw="Славка Петкова <slavka.petkova@acme.test>",
        second_seen_header="Cc",
        second_seen_raw="Slavka Petkova <slavka.petkova@acme.test>",
    ),
    StabilityControl(
        case_id="ident-204",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="RFC 5321 §2.4: the domain part is case-insensitive, so this is one exact mailbox",
        org_key="org_a",
        language="en",
        address="grace.hill@acme.test",
        first_seen_header="From",
        first_seen_raw="Grace Hill <grace.hill@ACME.test>",
        second_seen_header="From",
        second_seen_raw="Grace Hill <grace.hill@acme.test>",
    ),
    StabilityControl(
        case_id="ident-205",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="RFC 5322 folding whitespace around the angle-addr carries no identity meaning",
        org_key="org_a",
        language="bg",
        address="ivo.stoev@acme.test",
        first_seen_header="From",
        first_seen_raw="Иво Стоев <ivo.stoev@acme.test>",
        second_seen_header="From",
        second_seen_raw="Иво Стоев\r\n <ivo.stoev@acme.test>",
    ),
    StabilityControl(
        case_id="ident-206",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="quoted display name containing a comma must not split the observation",
        org_key="org_b",
        language="en",
        address="alice.ford@partner.test",
        first_seen_header="To",
        first_seen_raw='"Ford, Alice" <alice.ford@partner.test>',
        second_seen_header="To",
        second_seen_raw="Alice Ford <alice.ford@partner.test>",
    ),
    StabilityControl(
        case_id="ident-207",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="seen as sender, then as a copied recipient — the header role is not the identity",
        org_key="org_a",
        language="bg",
        address="rositsa.vodenicharova@acme.test",
        first_seen_header="From",
        first_seen_raw="Росица Воденичарова <rositsa.vodenicharova@acme.test>",
        second_seen_header="Cc",
        second_seen_raw="<rositsa.vodenicharova@acme.test>",
    ),
    StabilityControl(
        case_id="ident-208",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="To then Cc on an external counterparty address",
        org_key="org_a",
        language="en",
        address="brian.oconnor@example.test",
        first_seen_header="To",
        first_seen_raw="Brian O'Connor <brian.oconnor@example.test>",
        second_seen_header="Cc",
        second_seen_raw="Brian O'Connor <brian.oconnor@example.test>",
    ),
    StabilityControl(
        case_id="ident-209",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="a plus-tagged address is stable AS ITSELF; it must not be folded into its base",
        org_key="org_a",
        language="bg",
        address="martin.kirov+arhiv@example.test",
        first_seen_header="To",
        first_seen_raw="Мартин Киров <martin.kirov+arhiv@example.test>",
        second_seen_header="To",
        second_seen_raw="<martin.kirov+arhiv@example.test>",
    ),
    StabilityControl(
        case_id="ident-210",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="a role mailbox has a stable ROLE identity across observations (criterion 13: "
        "shared/role addresses get an appropriate identity type, never a person)",
        org_key="org_a",
        language="bg",
        address="sklad@acme.test",
        first_seen_header="From",
        first_seen_raw="Склад Акме <sklad@acme.test>",
        second_seen_header="To",
        second_seen_raw="<sklad@acme.test>",
    ),
    StabilityControl(
        case_id="ident-211",
        criterion_id=EXACT_ADDRESS_STABILITY,
        expected="stable",
        origin="From then Reply-To on one exact address",
        org_key="org_b",
        language="en",
        address="helen.ward@partner.test",
        first_seen_header="From",
        first_seen_raw="Helen Ward <helen.ward@partner.test>",
        second_seen_header="Reply-To",
        second_seen_raw="Helen Ward <helen.ward@partner.test>",
    ),
)
