"""
Role: the DATA half of the VIS fixture battery — the eight synthetic messages (six in org A, two
      in org B) with their recipient rows, attachment carriers, grants and promotion lineage, one
      per visibility STATE the criteria file names. This is the arrangement the VIS gate loads
      through the real write plane before any probe runs. The first four messages are authored
      here; the last four are in `vis_matrix_d` and the shared hashes, markers, origins and
      builders in `vis_matrix_c`. This module is the assembly point: it exports `MESSAGES` and
      re-exports the lexical markers `vis_matrix` imports.
Used by: tools.mem01_verify.fixtures.vis_matrix (which defines the probes over these messages and
      exports build_vis_matrix); the VIS gate evaluator loads MESSAGES, never this module's
      internals.
Depends on: tools.mem01_verify.fixtures.vis_matrix_a (record shapes, criterion ids, the org and
      persona roster), …vis_matrix_c (hashes, markers, origins, builders) and …vis_matrix_d (the
      second half of the arrangement). Nothing from `app.*` — a fixture must not be able to learn
      an expectation from the code under test (contract R12).
Key invariants:
  - Every string here is synthetic. Addresses live only under acme.test / partner.test /
    example.test; bodies are invented; both Bulgarian and English messages are present.
  - `lexical_marker` is a unique nonsense token embedded in the body — it is the query the
    lexical_search route uses, so a search result can be attributed to exactly one message.
  - ATT-A-06 and ATT-B-01 carry the SAME content_hash in two different orgs: identical bytes are
    two carrier rows, and the hash is never a bridge between tenants.
  - Restricted messages are readable only through a live acl_grant; M-A-04's grant to A2 is
    revoked (tombstone, `revoked=True`) and must stop matching immediately; M-A-03 and M-B-02 are
    org-visible and therefore carry the visibility_promotion lineage row PF-01 requires.
  - A3 is blind-copied on M-A-05 and holds NO grant: its bcc recipient row is not readable by
    anyone, including A3 itself, because A3 cannot see the restricted parent.
  - `expected` on these records describes the state that must hold AFTER loading, never a read
    outcome; read outcomes live on the Probe records in `vis_matrix`.
  - The B/C/D split is a file-size measure only (`.claude/rules/code-quality.md` A2) and carries
    no semantics. `MESSAGES` is the first four messages followed by `MESSAGES_D`, which is the
    order they were authored in, so a message moving between files — or the halves being
    concatenated the other way round — would change the battery digest with no record changed.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.vis_matrix_a import (
    A1_ADDRESS,
    A2_ADDRESS,
    APPROVER_A_USER_ID,
    CRITERION_NO_FORBIDDEN,
    CRITERION_NO_MISSING,
    EXTERNAL_PROCUREMENT,
    EXTERNAL_SALES,
    ORG_A_DOMAIN,
    MessageSpec,
    PromotionSpec,
)
from tools.mem01_verify.fixtures.vis_matrix_c import (
    _O_CHILD,
    _O_GRANT,
    _O_NO_GRANT,
    _O_PROMOTION,
    _O_REVOKED,
    HASH_A01,
    HASH_A02,
    HASH_A03,
    HASH_A04,
    MARKER_A01,
    MARKER_A02,
    MARKER_A03,
    MARKER_A04,
    MARKER_A05,
    MARKER_A06,
    MARKER_B01,
    _attachment,
    _grant,
    _recipient,
)
from tools.mem01_verify.fixtures.vis_matrix_d import MESSAGES_D

_MESSAGES_B: tuple[MessageSpec, ...] = (
    MessageSpec(
        case_id="vis-010",
        criterion_id=CRITERION_NO_MISSING,
        origin=_O_GRANT,
        expected="restricted_inbound_row_with_two_live_recipient_grants_A1_to_and_A2_cc",
        message_key="M-A-01",
        org_key="A",
        state="restricted_with_grant",
        visibility_scope="restricted",
        message_id=f"<vis-a-01@{ORG_A_DOMAIN}>",
        from_persona_key=None,
        from_address=EXTERNAL_SALES,
        subject="Оферта за доставка — тримесечие 3",
        body_text=f"Здравейте, изпращам актуалната оферта. Кодово име {MARKER_A01}. Поздрави.",
        lexical_marker=MARKER_A01,
        language="bg",
        recipients=(
            _recipient(
                "vis-020",
                "R-A-01-to-A1",
                "to",
                A1_ADDRESS,
                "Ирина Петрова",
                "A1",
                _O_CHILD,
                "visible_only_where_M-A-01_is_visible",
            ),
            _recipient(
                "vis-021",
                "R-A-01-cc-A2",
                "cc",
                A2_ADDRESS,
                "Nadia Fischer",
                "A2",
                _O_CHILD,
                "visible_only_where_M-A-01_is_visible",
            ),
        ),
        attachments=(
            _attachment(
                "vis-030",
                "ATT-A-01",
                "oferta-q3.pdf",
                "application/pdf",
                20_480,
                HASH_A01,
                "Оферта Q3 — синтетичен документ.",
                _O_CHILD,
            ),
        ),
        grants=(
            _grant("vis-040", "A1", "recipient", False, _O_GRANT),
            _grant("vis-041", "A2", "recipient", False, _O_GRANT),
        ),
        promotion=None,
    ),
    MessageSpec(
        case_id="vis-011",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_O_NO_GRANT,
        expected="restricted_row_with_one_live_grant_A1_only_A2_and_A3_hold_none",
        message_key="M-A-02",
        org_key="A",
        state="restricted_without_grant",
        visibility_scope="restricted",
        message_id=f"<vis-a-02@{ORG_A_DOMAIN}>",
        from_persona_key="A1",
        from_address=A1_ADDRESS,
        subject="Confidential: supplier terms review",
        body_text=f"Please keep this thread closed. Internal code {MARKER_A02}. Thanks.",
        lexical_marker=MARKER_A02,
        language="en",
        recipients=(
            _recipient(
                "vis-022",
                "R-A-02-to-ext",
                "to",
                EXTERNAL_PROCUREMENT,
                "Procurement",
                None,
                _O_CHILD,
                "visible_only_where_M-A-02_is_visible",
            ),
        ),
        attachments=(
            _attachment(
                "vis-031",
                "ATT-A-02",
                "supplier-terms.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                11_264,
                HASH_A02,
                "Supplier terms — synthetic draft.",
                _O_CHILD,
            ),
        ),
        grants=(_grant("vis-042", "A1", "sender", False, _O_GRANT),),
        promotion=None,
    ),
    MessageSpec(
        case_id="vis-012",
        criterion_id=CRITERION_NO_MISSING,
        origin=_O_PROMOTION,
        expected="org_visible_row_with_promotion_lineage_readable_by_every_org_A_reader",
        message_key="M-A-03",
        org_key="A",
        state="org_visible",
        visibility_scope="org",
        message_id=f"<vis-a-03@{ORG_A_DOMAIN}>",
        from_persona_key="A1",
        from_address=A1_ADDRESS,
        subject="Общи правила за отпуски — публикувано",
        body_text=f"Правилата важат за целия екип. Идентификатор {MARKER_A03}.",
        lexical_marker=MARKER_A03,
        language="bg",
        recipients=(
            _recipient(
                "vis-023",
                "R-A-03-to-A2",
                "to",
                A2_ADDRESS,
                "Nadia Fischer",
                "A2",
                _O_CHILD,
                "visible_to_every_org_A_reader_because_the_parent_is_org_visible",
            ),
        ),
        attachments=(
            _attachment(
                "vis-032",
                "ATT-A-03",
                "pravila.pdf",
                "application/pdf",
                30_720,
                HASH_A03,
                "Правила за отпуски — синтетичен документ.",
                _O_CHILD,
            ),
        ),
        grants=(_grant("vis-043", "A1", "sender", False, _O_GRANT),),
        promotion=PromotionSpec(
            case_id="vis-051",
            criterion_id=CRITERION_NO_MISSING,
            origin=_O_PROMOTION,
            expected="lineage_row_present_so_the_org_scope_flip_is_legal",
            approved_by_user_id=APPROVER_A_USER_ID,
            from_scope="restricted",
            to_scope="org",
        ),
    ),
    MessageSpec(
        case_id="vis-013",
        criterion_id=CRITERION_NO_FORBIDDEN,
        origin=_O_REVOKED,
        expected="restricted_row_whose_A2_grant_is_a_revoked_tombstone_A1_grant_still_live",
        message_key="M-A-04",
        org_key="A",
        state="grant_revoked",
        visibility_scope="restricted",
        message_id=f"<vis-a-04@{ORG_A_DOMAIN}>",
        from_persona_key="A1",
        from_address=A1_ADDRESS,
        subject="Access withdrawn: pricing model",
        body_text=f"Pricing model attached. Reference {MARKER_A04}. Handle with care.",
        lexical_marker=MARKER_A04,
        language="en",
        recipients=(
            _recipient(
                "vis-024",
                "R-A-04-to-A2",
                "to",
                A2_ADDRESS,
                "Nadia Fischer",
                "A2",
                _O_CHILD,
                "invisible_to_A2_once_the_grant_is_revoked",
            ),
        ),
        attachments=(
            _attachment(
                "vis-033",
                "ATT-A-04",
                "pricing.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                8_192,
                HASH_A04,
                "Pricing model — synthetic sheet.",
                _O_CHILD,
            ),
        ),
        grants=(
            _grant("vis-044", "A1", "sender", False, _O_GRANT),
            _grant("vis-045", "A2", "recipient", True, _O_REVOKED),
        ),
        promotion=None,
    ),
)

MESSAGES: tuple[MessageSpec, ...] = _MESSAGES_B + MESSAGES_D

__all__ = [
    "MARKER_A01",
    "MARKER_A02",
    "MARKER_A03",
    "MARKER_A04",
    "MARKER_A05",
    "MARKER_A06",
    "MARKER_B01",
    "MESSAGES",
]
