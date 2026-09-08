"""
Role: Seals LEAK_GROUPS_V1 (contract §7, §1.4) — every edge kind (reply, reference, collision,
      sibling, attachment, template) with its negative control, the two property-based
      exclusions (inline image, designated boilerplate), the review trigger at exactly 25 vs 26
      carriers (a trigger, never a cut), the partition invariant, order independence, group ids,
      the emitted files, and `compute_leakage_groups` on the six-email probe org.
Used by: the seal review; the mutation sample (§14.2 item 2b).
Depends on: tools.mem01_verify.leakage and .db (imported inside each test);
      tests.tools.mem01_verify.reference (group ids, file readers).
Key invariants:
  - Every "joined" assertion is paired with a "not joined" control built from the same shape.
"""

from __future__ import annotations

import random
import re
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.conftest import SESSION_LOOP, InstrumentLoader, ProbeCorpusFactory

EDGE_KINDS = ("reply", "reference", "collision", "sibling", "attachment", "template")
HASH_A = sha256(b"oracle attachment a").hexdigest()
BODY_A = sha256(b"oracle body a").hexdigest()


def _node(
    leakage: object,
    *,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: tuple[str, ...] = (),
    body: str | None = None,
) -> object:
    return leakage.EmailNode(  # type: ignore[attr-defined]
        email_id=uuid4(),
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        normalized_body_sha256=body,
    )


def _carrier(
    leakage: object,
    node: object,
    content_hash: str,
    *,
    content_type: str = "application/pdf",
    is_inline: bool = False,
    status: str = "extracted",
) -> object:
    return leakage.AttachmentCarrier(  # type: ignore[attr-defined]
        email_id=node.email_id,
        content_hash=content_hash,
        content_type=content_type,
        is_inline=is_inline,
        extraction_status=status,
    )


def _partition(result: object) -> set[frozenset[UUID]]:
    return {frozenset(group.email_ids) for group in result.groups}  # type: ignore[attr-defined]


def test_constants_and_version(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")

    assert leakage.LEAK_GROUPS_VERSION == "LEAK_GROUPS_V1"
    assert leakage.UBIQUITY_REVIEW_TRIGGER == 25


@pytest.mark.parametrize(
    "kind",
    [
        "reply",
        "reference",
        "collision",
        "sibling_in_reply_to",
        "sibling_references",
        "sibling_cross_header",
        "attachment",
        "template",
    ],
)
def test_each_edge_kind_joins_the_pair_and_leaves_the_control_alone(
    instrument: InstrumentLoader, kind: str
) -> None:
    leakage = instrument("leakage")
    carriers: list[object] = []
    if kind == "reply":
        a = _node(leakage, message_id="a@acme.test")
        b = _node(leakage, message_id="b@acme.test", in_reply_to="a@acme.test")
    elif kind == "reference":
        a = _node(leakage, message_id="a@acme.test")
        b = _node(leakage, message_id="b@acme.test", references=("x@acme.test", "a@acme.test"))
    elif kind == "collision":
        a = _node(leakage, message_id="same@acme.test")
        b = _node(leakage, message_id="same@acme.test")
    elif kind == "sibling_in_reply_to":
        a = _node(leakage, message_id="a@acme.test", in_reply_to="parent@partner.test")
        b = _node(leakage, message_id="b@acme.test", in_reply_to="parent@partner.test")
    elif kind == "sibling_references":
        a = _node(leakage, message_id="a@acme.test", references=("gone@partner.test",))
        b = _node(leakage, message_id="b@acme.test", references=("gone@partner.test", "z@x.test"))
    elif kind == "sibling_cross_header":
        # §16.11: ancestors(a) ∩ ancestors(b) ≠ ∅ — in_reply_to of one is a references token
        # of the other, neither id present in the corpus
        a = _node(leakage, message_id="a@acme.test", in_reply_to="parent@partner.test")
        b = _node(
            leakage,
            message_id="b@acme.test",
            references=("root@partner.test", "parent@partner.test"),
        )
    elif kind == "attachment":
        a = _node(leakage, message_id="a@acme.test")
        b = _node(leakage, message_id="b@acme.test")
        carriers = [_carrier(leakage, a, HASH_A), _carrier(leakage, b, HASH_A)]
    else:
        a = _node(leakage, message_id="a@acme.test", body=BODY_A)
        b = _node(leakage, message_id="b@acme.test", body=BODY_A)
    control = _node(leakage, message_id="c@acme.test")

    result = leakage.group_rows([a, b, control], carriers)

    assert _partition(result) == {
        frozenset({a.email_id, b.email_id}),
        frozenset({control.email_id}),
    }
    joined = next(g for g in result.groups if g.size == 2)
    edge_kind = kind.split("_")[0]
    assert joined.edge_counts[edge_kind] >= 1
    assert set(joined.edge_counts) == set(EDGE_KINDS)
    if kind == "collision":
        assert result.collision_edges == 1
    if kind.startswith("sibling"):
        assert result.sibling_edges == 1
    if kind == "template":
        assert result.template_edges == 1
    if kind == "attachment":
        assert joined.attachment_hashes == (HASH_A,)


def test_null_in_reply_to_and_null_bodies_never_join(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    a = _node(leakage, message_id="a@acme.test", in_reply_to=None, body=None)
    b = _node(leakage, message_id="b@acme.test", in_reply_to=None, body=None)

    result = leakage.group_rows([a, b], [])

    assert _partition(result) == {frozenset({a.email_id}), frozenset({b.email_id})}
    assert result.sibling_edges == 0 and result.template_edges == 0
    assert result.singleton_count == 2


def test_inline_image_is_the_only_property_exclusion(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    a, b = _node(leakage, message_id="a@acme.test"), _node(leakage, message_id="b@acme.test")
    inline_png = [
        _carrier(leakage, a, HASH_A, content_type="image/png", is_inline=True),
        _carrier(leakage, b, HASH_A, content_type="image/png", is_inline=True),
    ]
    attached_png = [
        _carrier(leakage, a, HASH_A, content_type="image/png", is_inline=False),
        _carrier(leakage, b, HASH_A, content_type="image/png", is_inline=False),
    ]
    inline_pdf = [
        _carrier(leakage, a, HASH_A, is_inline=True),
        _carrier(leakage, b, HASH_A, is_inline=True),
    ]
    skipped = [
        _carrier(leakage, a, HASH_A, status="skipped_nondocument"),
        _carrier(leakage, b, HASH_A, status="skipped_nondocument"),
    ]
    separate = {frozenset({a.email_id}), frozenset({b.email_id})}
    together = {frozenset({a.email_id, b.email_id})}

    assert _partition(leakage.group_rows([a, b], inline_png)) == separate
    assert _partition(leakage.group_rows([a, b], attached_png)) == together
    assert _partition(leakage.group_rows([a, b], inline_pdf)) == together
    assert _partition(leakage.group_rows([a, b], skipped)) == together


def test_designated_boilerplate_excludes_a_hash_and_is_reported(
    instrument: InstrumentLoader,
) -> None:
    leakage = instrument("leakage")
    a, b = _node(leakage, message_id="a@acme.test"), _node(leakage, message_id="b@acme.test")
    carriers = [_carrier(leakage, a, HASH_A), _carrier(leakage, b, HASH_A)]

    designated = leakage.group_rows([a, b], carriers, designated_boilerplate=frozenset({HASH_A}))
    plain = leakage.group_rows([a, b], carriers)

    assert _partition(designated) == {frozenset({a.email_id}), frozenset({b.email_id})}
    assert designated.designated_boilerplate_applied == (HASH_A,)
    assert _partition(plain) == {frozenset({a.email_id, b.email_id})}
    assert plain.designated_boilerplate_applied == ()


@pytest.mark.parametrize(("carrier_count", "listed"), [(25, False), (26, True)])
def test_review_trigger_lists_hashes_above_25_carriers_but_still_joins(
    instrument: InstrumentLoader, carrier_count: int, listed: bool
) -> None:
    leakage = instrument("leakage")
    nodes = [_node(leakage, message_id=f"n{i}@acme.test") for i in range(carrier_count)]
    carriers = [_carrier(leakage, node, HASH_A) for node in nodes]

    result = leakage.group_rows(nodes, carriers)

    assert _partition(result) == {frozenset(node.email_id for node in nodes)}
    assert (HASH_A in result.review_trigger_hashes) is listed
    if listed:
        assert result.review_trigger_hashes[HASH_A] == carrier_count
    assert 25 in set(result.constants.values())


def test_chains_are_transitive_and_partition_invariants_hold(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    chain = [_node(leakage, message_id="c0@acme.test")]
    for index in range(1, 4):
        chain.append(
            _node(leakage, message_id=f"c{index}@acme.test", in_reply_to=f"c{index - 1}@acme.test")
        )
    pair_a = _node(leakage, message_id="p@acme.test", body=BODY_A)
    pair_b = _node(leakage, message_id="q@acme.test", body=BODY_A)
    singles = [_node(leakage, message_id=f"s{i}@acme.test") for i in range(10)]
    everyone = chain + [pair_a, pair_b] + singles

    result = leakage.group_rows(everyone, [])

    ids = [email_id for group in result.groups for email_id in group.email_ids]
    assert sorted(ids) == sorted(node.email_id for node in everyone)
    assert frozenset(node.email_id for node in chain) in _partition(result)
    assert result.singleton_count == 10
    assert dict(result.size_histogram) == {4: 1, 2: 1, 1: 10}
    sizes = sorted((group.size for group in result.groups), reverse=True)
    assert tuple(result.largest_sizes) == tuple(sizes[:10])
    assert all(group.size == len(group.email_ids) for group in result.groups)


def test_group_ids_and_orderings_follow_the_contract(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    a = _node(leakage, message_id="a@acme.test")
    b = _node(leakage, message_id="b@acme.test", in_reply_to="a@acme.test")
    c = _node(leakage, message_id="c@acme.test")

    result = leakage.group_rows([b, c, a], [])

    for group in result.groups:
        assert group.group_id == reference.expected_group_id(group.email_ids)
        assert list(group.email_ids) == sorted(group.email_ids)
    assert [group.group_id for group in result.groups] == sorted(g.group_id for g in result.groups)
    assert result.version == "LEAK_GROUPS_V1" and result.input_corpus_digest is None


def test_group_rows_is_order_independent(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    rng = random.Random(7)
    nodes = [
        _node(
            leakage,
            message_id=f"m{i}@acme.test",
            in_reply_to=f"m{i - 1}@acme.test" if i % 3 else None,
            body=BODY_A if i % 5 == 0 else None,
        )
        for i in range(30)
    ]
    carriers = [
        _carrier(leakage, node, HASH_A if i % 4 == 0 else f"h{i}") for i, node in enumerate(nodes)
    ]
    shuffled_nodes, shuffled_carriers = list(nodes), list(carriers)
    rng.shuffle(shuffled_nodes)
    rng.shuffle(shuffled_carriers)

    assert leakage.group_rows(nodes, carriers) == leakage.group_rows(
        shuffled_nodes, shuffled_carriers
    )


def test_write_leakage_emits_groups_file_and_summary(
    instrument: InstrumentLoader, tmp_path: Path
) -> None:
    leakage = instrument("leakage")
    a = _node(leakage, message_id="a@acme.test")
    b = _node(leakage, message_id="b@acme.test", in_reply_to="a@acme.test")
    result = leakage.group_rows([a, b], [])

    leakage.write_leakage(result, tmp_path)

    groups = reference.read_jsonl(tmp_path / "leakage_groups.jsonl")
    assert len(groups) == 1 and groups[0]["size"] == 2
    assert {"group_id", "email_ids", "size", "edge_counts", "attachment_hashes"} <= set(groups[0])
    assert groups[0]["group_id"] == reference.expected_group_id([a.email_id, b.email_id])
    summary = reference.read_json(tmp_path / "leakage.summary.json")
    assert summary["version"] == "LEAK_GROUPS_V1"
    for key in (
        "constants",
        "collision_edges",
        "sibling_edges",
        "template_edges",
        "review_trigger_hashes",
        "designated_boilerplate_applied",
        "corpus_digest",
    ):
        assert key in summary
    assert re.search(
        r"near[- ]duplicate", reference.read_text(tmp_path / "leakage.summary.json").lower()
    )


@SESSION_LOOP
async def test_compute_leakage_groups_on_the_small_org_matches_the_seeded_partition(
    instrument: InstrumentLoader, probe_corpus: ProbeCorpusFactory
) -> None:
    corpus = await probe_corpus()
    db = instrument("db")
    leakage = instrument("leakage")
    small = corpus.small

    async with db.readonly_corpus_snapshot(small.org_id, database=corpus.database) as conn:
        result = await leakage.compute_leakage_groups(conn, small.org_id)

    assert _partition(result) == set(small.expected_groups)
    assert result.collision_edges >= 1 and result.sibling_edges >= 1 and result.template_edges >= 1
    big_group = next(group for group in result.groups if group.size == 5)
    assert big_group.edge_counts["attachment"] >= 1 and big_group.edge_counts["reply"] >= 1
    assert result.input_corpus_digest and re.fullmatch(r"[0-9a-f]{64}", result.input_corpus_digest)
    assert sum(group.size for group in result.groups) == small.email_count


def test_review_trigger_counts_distinct_carrier_emails_not_attachment_rows(
    instrument: InstrumentLoader,
) -> None:
    leakage = instrument("leakage")
    duplicated = [_node(leakage, message_id=f"d{i}@acme.test") for i in range(13)]
    rows = [_carrier(leakage, node, HASH_A) for node in duplicated for _ in range(2)]  # 26 rows
    distinct = [_node(leakage, message_id=f"e{i}@acme.test") for i in range(26)]

    below = leakage.group_rows(duplicated, rows)
    above = leakage.group_rows(distinct, [_carrier(leakage, node, HASH_A) for node in distinct])

    assert HASH_A not in below.review_trigger_hashes  # 13 carrier emails, not 26
    assert dict(above.review_trigger_hashes) == {HASH_A: 26}  # positive control
    assert _partition(below) == {frozenset(node.email_id for node in duplicated)}


def test_null_message_ids_never_collide(instrument: InstrumentLoader) -> None:
    leakage = instrument("leakage")
    first, second = _node(leakage, message_id=None), _node(leakage, message_id=None)
    same, same_again = (_node(leakage, message_id="same@acme.test") for _ in range(2))

    result = leakage.group_rows([first, second, same, same_again], [])

    assert _partition(result) == {
        frozenset({first.email_id}),
        frozenset({second.email_id}),
        frozenset({same.email_id, same_again.email_id}),
    }
    assert result.collision_edges == 1
