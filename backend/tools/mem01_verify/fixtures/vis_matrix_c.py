"""
Role: the shared vocabulary the VIS arrangement is written in — the synthetic content hashes, the
      per-message lexical markers, the origin strings naming the rule each arrangement pins, and
      the three private builders (`_attachment`, `_recipient`, `_grant`) that stamp the criterion
      id and the post-load expectation onto every child row. It holds no messages of its own.
Used by: tools.mem01_verify.fixtures.vis_matrix_b and …vis_matrix_d (the two halves of MESSAGES);
      the markers reach `vis_matrix` re-exported through `vis_matrix_b`.
Depends on: tools.mem01_verify.fixtures.vis_matrix_a (record shapes and criterion ids). Nothing
      from `app.*` — a fixture must not be able to learn an expectation from the code under test
      (contract R12).
Key invariants:
  - This module exists only to keep each VIS fixture module under the file-size ceiling of
    `.claude/rules/code-quality.md` A2. It moved no record and changed no value, so the battery
    digest is unchanged by it.
  - Every string here is synthetic. The hashes are obviously fake 64-hex-character values and the
    markers are invented tokens; neither is derived from corpus bytes.
  - `HASH_SHARED` is deliberately carried by one org A message AND one org B message: identical
    bytes are two carrier rows and the hash is never a bridge between tenants.
  - Each marker shares no substring with another, so a lexical_search hit is attributable to
    exactly one message.
  - `criterion_id` is assigned by one declared rule in all three builders and is never a
    parameter: a child row (attachment or recipient) pins `vis.no_wrong_inherited_relations`, and
    a grant pins `vis.no_forbidden_rows` when it is a revoked tombstone, `vis.no_missing_allowed`
    when it is live.
  - `expected` is NOT uniform across the three: `_attachment` fixes it, `_grant` derives it from
    `revoked`, and `_recipient` takes it from the caller — a recipient row's post-load state
    depends on the kind of recipient it is, which only the message author knows.
"""

from __future__ import annotations

from typing import Literal

from tools.mem01_verify.fixtures.vis_matrix_a import (
    CRITERION_NO_FORBIDDEN,
    CRITERION_NO_MISSING,
    CRITERION_NO_WRONG_INHERITED,
    AttachmentSpec,
    GrantSpec,
    RecipientSpec,
)

# --- synthetic content hashes (obviously fake, 64 hex chars) ----------------------------------
HASH_A01 = "0a01" * 16
HASH_A02 = "0a02" * 16
HASH_A03 = "0a03" * 16
HASH_A04 = "0a04" * 16
HASH_A05 = "0a05" * 16
HASH_SHARED = "5eed" * 16  # the same bytes carried by an org A message AND an org B message

# --- lexical markers: one invented token per message, sharing no substring with another, so a
# --- search hit is attributable to exactly one message ----------------------------------------
MARKER_A01 = "ЛАЗУРНОКОТВА-А01"
MARKER_A02 = "AMBERGATE-A02"
MARKER_A03 = "ОБЩОПРАВИЛО-А03"
MARKER_A04 = "CINDERVAULT-A04"
MARKER_A05 = "СКРИТОКОПИЕ-А05"
MARKER_A06 = "QUARTZLEDGER-A06"
MARKER_B01 = "OBSIDIANFERRY-B01"
MARKER_B02 = "ЖЪЛТОЯДРО-Б02"

# --- origins: the rule each arrangement pins --------------------------------------------------
_O_GRANT = (
    "PF-01: a restricted message is retrievable only through a live acl_grant (revoked_at IS "
    "NULL) matching the reader session's person"
)
_O_NO_GRANT = (
    "PF-01 unknown => deny: an org A person without a live grant on a restricted row is not "
    "a reader of it, whatever else they can see in the same tenant"
)
_O_REVOKED = (
    "PF-01 revocation is a tombstone: setting revoked_at stops the grant matching immediately, "
    "the row survives as the works-council trail"
)
_O_PROMOTION = (
    "PF-01 AC5: visibility_scope='org' on a restricted-origin row is legal only with a "
    "visibility_promotion lineage row naming the approving human and the audit row"
)
_O_BCC = (
    "criterion sheet VIS: BCC membership never appears in any row a non-BCC persona can read, "
    "and a blind-copied person holds no grant, so the parent stays invisible to them too"
)
_O_CHILD = (
    "criterion sheet VIS (no wrong inherited relations): a child row is visible exactly when its "
    "parent message is visible to the same reader, never on its own"
)
_O_SHARED = (
    "criterion sheet VIS (shared attachments): identical attachment bytes in two orgs are two "
    "carrier rows; an equal content_hash is not a cross-tenant bridge"
)


def _attachment(
    case_id: str,
    key: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    content_hash: str,
    text: str,
    origin: str,
) -> AttachmentSpec:
    """Build one delivered (extracted) attachment carrier; every field is synthetic."""
    return AttachmentSpec(
        case_id=case_id,
        criterion_id=CRITERION_NO_WRONG_INHERITED,
        origin=origin,
        expected="stored_as_a_carrier_row_inheriting_the_parent_message_visibility",
        attachment_key=key,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        content_hash=content_hash,
        is_inline=False,
        extraction_status="extracted",
        extractor_name="synthetic_fixture_extractor",
        extractor_version="vis-fixture-1",
        extracted_text=text,
    )


def _recipient(
    case_id: str,
    key: str,
    kind: Literal["to", "cc", "bcc"],
    address: str,
    display_name: str | None,
    persona_key: str | None,
    origin: str,
    expected: str,
) -> RecipientSpec:
    """Build one email_recipient child row."""
    return RecipientSpec(
        case_id=case_id,
        criterion_id=CRITERION_NO_WRONG_INHERITED,
        origin=origin,
        expected=expected,
        recipient_key=key,
        kind=kind,
        address=address,
        display_name=display_name,
        persona_key=persona_key,
    )


def _grant(
    case_id: str,
    persona_key: str,
    provenance: Literal["recipient", "sender", "owner"],
    revoked: bool,
    origin: str,
) -> GrantSpec:
    """Build one acl_grant row (live, or a revoked tombstone)."""
    return GrantSpec(
        case_id=case_id,
        criterion_id=CRITERION_NO_FORBIDDEN if revoked else CRITERION_NO_MISSING,
        origin=origin,
        expected="stops_matching_immediately" if revoked else "admits_exactly_its_holder",
        persona_key=persona_key,
        provenance=provenance,
        revoked=revoked,
    )
