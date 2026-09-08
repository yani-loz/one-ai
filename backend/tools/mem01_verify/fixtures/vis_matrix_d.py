"""
Role: the DATA half of the VIS arrangement, part 2 — the last four synthetic messages (the
      bcc-only message, the shared-attachment pair across org A and org B, and the org B
      promotion) with their recipient rows, attachment carriers, grants and promotion lineage.
      It is the continuation of the first four messages in `vis_matrix_b`, which concatenates the
      two into `MESSAGES`.
Used by: tools.mem01_verify.fixtures.vis_matrix_b (the aggregation point); the VIS gate evaluator
      loads `MESSAGES` from `vis_matrix`, never this module.
Depends on: tools.mem01_verify.fixtures.vis_matrix_a (record shapes, criterion ids, the org and
      persona roster) and …vis_matrix_c (hashes, markers, origins, builders). Nothing from
      `app.*` — a fixture must not be able to learn an expectation from the code under test
      (contract R12).
Key invariants:
  - The B/D split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries no
    semantics. `MESSAGES_D` is a contiguous SUFFIX of the arrangement: the concatenation in
    `vis_matrix_b` reproduces the authored order exactly, so the battery digest does not move
    because a message changed file.
  - The arrangement's full invariant set is stated once, in `vis_matrix_b`, and governs the
    messages here.
  - A3 is blind-copied on M-A-05 and holds NO grant: its bcc recipient row is not readable by
    anyone, including A3 itself, because A3 cannot see the restricted parent.
  - ATT-A-06 and ATT-B-01 carry the SAME content_hash in two different orgs: identical bytes are
    two carrier rows, and the hash is never a bridge between tenants.
  - `expected` on these records describes the state that must hold AFTER loading, never a read
    outcome; read outcomes live on the Probe records in `vis_matrix`.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.vis_matrix_a import (
    A1_ADDRESS,
    A2_ADDRESS,
    A3_ADDRESS,
    APPROVER_B_USER_ID,
    B1_ADDRESS,
    CRITERION_NO_FORBIDDEN,
    CRITERION_NO_MISSING,
    EXTERNAL_OPS,
    EXTERNAL_SALES,
    ORG_A_DOMAIN,
    ORG_B_DOMAIN,
    MessageSpec,
    PromotionSpec,
)
from tools.mem01_verify.fixtures.vis_matrix_c import (
    _O_BCC,
    _O_CHILD,
    _O_GRANT,
    _O_PROMOTION,
    _O_SHARED,
    HASH_A05,
    HASH_SHARED,
    MARKER_A05,
    MARKER_A06,
    MARKER_B01,
    MARKER_B02,
    _attachment,
    _grant,
    _recipient,
)

MESSAGES_D: tuple[MessageSpec, ...] = (
    MessageSpec(
        case_id="vis-014",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_O_BCC,
        expected="restricted_row_with_a_bcc_recipient_row_and_no_grant_for_the_bcc_person",
        message_key="M-A-05",
        org_key="A",
        state="bcc_only",
        visibility_scope="restricted",
        message_id=f"<vis-a-05@{ORG_A_DOMAIN}>",
        from_persona_key="A1",
        from_address=A1_ADDRESS,
        subject="Дискретно: преглед на договора",
        body_text=f"Моля, прегледай тихо. Означение {MARKER_A05}. Благодаря.",
        lexical_marker=MARKER_A05,
        language="bg",
        recipients=(
            _recipient(
                "vis-025",
                "R-A-05-to-A2",
                "to",
                A2_ADDRESS,
                "Nadia Fischer",
                "A2",
                _O_CHILD,
                "visible_where_M-A-05_is_visible",
            ),
            _recipient(
                "vis-026",
                "R-A-05-bcc-A3",
                "bcc",
                A3_ADDRESS,
                "Явор Ковачев",
                "A3",
                _O_BCC,
                "invisible_to_every_reader_including_the_bcc_person_itself",
            ),
        ),
        attachments=(
            _attachment(
                "vis-034",
                "ATT-A-05",
                "dogovor.pdf",
                "application/pdf",
                16_384,
                HASH_A05,
                "Договор — синтетичен документ.",
                _O_CHILD,
            ),
        ),
        grants=(
            _grant("vis-046", "A1", "sender", False, _O_GRANT),
            _grant("vis-047", "A2", "recipient", False, _O_GRANT),
        ),
        promotion=None,
    ),
    MessageSpec(
        case_id="vis-015",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_O_SHARED,
        expected="org_A_restricted_carrier_of_the_shared_bytes_grant_A1_only",
        message_key="M-A-06",
        org_key="A",
        state="shared_attachment_cross_org",
        visibility_scope="restricted",
        message_id=f"<vis-a-06@{ORG_A_DOMAIN}>",
        from_persona_key="A1",
        from_address=A1_ADDRESS,
        subject="Shared spec (org A copy)",
        body_text=f"The joint spec is attached. Tag {MARKER_A06}.",
        lexical_marker=MARKER_A06,
        language="en",
        recipients=(
            _recipient(
                "vis-027",
                "R-A-06-to-ext",
                "to",
                EXTERNAL_SALES,
                "Sales Desk",
                None,
                _O_CHILD,
                "visible_only_where_M-A-06_is_visible",
            ),
        ),
        attachments=(
            _attachment(
                "vis-035",
                "ATT-A-06",
                "joint-spec.pdf",
                "application/pdf",
                40_960,
                HASH_SHARED,
                "Joint spec — synthetic shared bytes.",
                _O_SHARED,
            ),
        ),
        grants=(_grant("vis-048", "A1", "sender", False, _O_GRANT),),
        promotion=None,
    ),
    MessageSpec(
        case_id="vis-016",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_O_SHARED,
        expected="org_B_restricted_carrier_of_the_same_bytes_grant_B1_only",
        message_key="M-B-01",
        org_key="B",
        state="shared_attachment_cross_org",
        visibility_scope="restricted",
        message_id=f"<vis-b-01@{ORG_B_DOMAIN}>",
        from_persona_key="B1",
        from_address=B1_ADDRESS,
        subject="Shared spec (org B copy)",
        body_text=f"Same file, our tenant. Tag {MARKER_B01}.",
        lexical_marker=MARKER_B01,
        language="en",
        recipients=(
            _recipient(
                "vis-028",
                "R-B-01-to-ext",
                "to",
                EXTERNAL_OPS,
                "Operations",
                None,
                _O_CHILD,
                "visible_only_where_M-B-01_is_visible",
            ),
        ),
        attachments=(
            _attachment(
                "vis-036",
                "ATT-B-01",
                "joint-spec.pdf",
                "application/pdf",
                40_960,
                HASH_SHARED,
                "Joint spec — synthetic shared bytes.",
                _O_SHARED,
            ),
        ),
        grants=(_grant("vis-049", "B1", "sender", False, _O_GRANT),),
        promotion=None,
    ),
    MessageSpec(
        case_id="vis-017",
        criterion_id=CRITERION_NO_MISSING,
        origin=_O_PROMOTION,
        expected="org_B_org_visible_row_with_lineage_readable_by_B1_and_by_no_org_A_reader",
        message_key="M-B-02",
        org_key="B",
        state="org_visible",
        visibility_scope="org",
        message_id=f"<vis-b-02@{ORG_B_DOMAIN}>",
        from_persona_key="B1",
        from_address=B1_ADDRESS,
        subject="Вътрешно обявление (организация Б)",
        body_text=f"Обявление за целия екип. Означение {MARKER_B02}.",
        lexical_marker=MARKER_B02,
        language="bg",
        recipients=(
            _recipient(
                "vis-029",
                "R-B-02-to-ext",
                "to",
                EXTERNAL_OPS,
                "Operations",
                None,
                _O_CHILD,
                "visible_to_org_B_readers_only",
            ),
        ),
        attachments=(),
        grants=(_grant("vis-050", "B1", "sender", False, _O_GRANT),),
        promotion=PromotionSpec(
            case_id="vis-052",
            criterion_id=CRITERION_NO_FORBIDDEN,
            origin=_O_PROMOTION,
            expected="org_visibility_is_scoped_to_org_B_and_never_widens_across_tenants",
            approved_by_user_id=APPROVER_B_USER_ID,
            from_scope="restricted",
            to_scope="org",
        ),
    ),
)
