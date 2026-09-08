"""
Role: LEAK_GROUPS_V1 — the leakage-group instrument of contract §7. Partitions a set of emails
      into split-safe groups by joining any pair that shares a reply, reference, message-id
      collision, ancestor sibling (§16.11), non-boilerplate attachment hash, or an identical
      normalized body, and reports the ubiquity review list the founder designates boilerplate
      from. Deliberately separate from production threading: conservative over-merge is correct
      here, a missed merge is not.
Used by: the release `instruments` subcommand and the leakage report under a draft release
      (wave 2); the sealed oracle `tests/tools/mem01_verify/test_leakage.py`.
Depends on: nothing for the pure core — `group_rows` is a pure function over the two row shapes
      below. The wave-2 half adds tools.mem01_verify.leakage_db (the snapshot reads and the two
      emitted files) and .corpus_identity (the `CORPUS_DIGEST_V1` stamp), so every database read
      and every file write still lives outside the core.
Key invariants:
  - Every email belongs to exactly one group; the union of the groups equals the input roster.
  - `group_rows` is order independent: the same rows in any order yield an equal `LeakageResult`.
  - `UBIQUITY_REVIEW_TRIGGER` is a REVIEW list, never a classification — a hash above it still
    joins; only a frozen `designated_boilerplate` designation, or an inline image, excludes one.
  - Near-duplicate similarity clustering is NOT implemented in V1 (exact normalized-body
    identity only); the wave-2 summary states this in prose.
  - Edge counts are KEY-CLASS MULTIPLICITIES, not distinct pairs: each token / content hash /
    body digest that joins `n` emails contributes `C(n, 2)` to its kind. The two coincide for
    `collision` and `template` (one key per email); a pair joined by several shared ancestor
    tokens or attachment hashes is counted once per key. Chosen so the counts stay O(classes)
    instead of materializing every pair of a corpus-wide component; the wave-2 summary says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tools.mem01_verify import corpus_identity, leakage_db

# The §1.3 public surface lives on THIS module; the writer's body is in leakage_db (file size).
from tools.mem01_verify.leakage_db import write_leakage as write_leakage

LEAK_GROUPS_VERSION = "LEAK_GROUPS_V1"
UBIQUITY_REVIEW_TRIGGER = 25

EDGE_KINDS: tuple[str, ...] = (
    "reply",
    "reference",
    "collision",
    "sibling",
    "attachment",
    "template",
)
LARGEST_SIZES_REPORTED = 10


# ── public row shapes and results (contract §1.4) ─────────────────────────────────────────


@dataclass(frozen=True)
class EmailNode:
    """One email as the instrument sees it: identity headers plus the normalized body digest."""

    email_id: UUID
    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    normalized_body_sha256: str | None


@dataclass(frozen=True)
class AttachmentCarrier:
    """One attachment row: which email carries which content hash, and how it is carried."""

    email_id: UUID
    content_hash: str
    content_type: str
    is_inline: bool
    extraction_status: str


@dataclass(frozen=True)
class LeakageGroup:
    """One connected component: its members, its size, and why its members were joined."""

    group_id: str
    email_ids: tuple[UUID, ...]
    size: int
    edge_counts: Mapping[str, int]
    attachment_hashes: tuple[str, ...]


@dataclass(frozen=True)
class LeakageResult:
    """The full partition plus the aggregates the summary file and the founder review need."""

    version: str
    groups: tuple[LeakageGroup, ...]
    singleton_count: int
    size_histogram: Mapping[int, int]
    largest_sizes: tuple[int, ...]
    review_trigger_hashes: Mapping[str, int]
    designated_boilerplate_applied: tuple[str, ...]
    collision_edges: int
    sibling_edges: int
    template_edges: int
    input_corpus_digest: str | None
    constants: Mapping[str, int]


# ── internal edge bookkeeping ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _EdgeClass:
    """One key class that joins its members: the kind, how many pairs it contributes, who."""

    kind: str
    count: int
    members: tuple[int, ...]


@dataclass(frozen=True)
class _EmailKeys:
    """The header indices every edge kind is derived from (token → node indices)."""

    by_message_id: Mapping[str, list[int]]
    reply_children: Mapping[str, list[int]]
    reference_holders: Mapping[str, list[int]]
    ancestors_by_token: Mapping[str, list[int]]
    by_body: Mapping[str, list[int]]


def _pair_count(size: int) -> int:
    """Unordered pairs inside a class of `size` distinct members."""
    return size * (size - 1) // 2


def _find(parent: list[int], node: int) -> int:
    """Union-find root of `node`, with path compression."""
    root = node
    while parent[root] != root:
        root = parent[root]
    while parent[node] != root:
        parent[node], node = root, parent[node]
    return root


def _union_all(parent: list[int], members: Iterable[int]) -> None:
    """Merge every member of one key class into a single component."""
    iterator = iter(members)
    first = next(iterator, None)
    if first is None:
        return
    root = _find(parent, first)
    for member in iterator:
        other = _find(parent, member)
        if other != root:
            parent[other] = root


def _bucket(mapping: dict[str, list[int]], key: str | None, index: int) -> None:
    """Record `index` under `key`, ignoring absent (null or empty) tokens."""
    if key:
        mapping.setdefault(key, []).append(index)


def _index_email_keys(nodes: Sequence[EmailNode]) -> _EmailKeys:
    """Bucket every node by the tokens the six edge kinds of §7 are derived from."""
    by_message_id: dict[str, list[int]] = {}
    reply_children: dict[str, list[int]] = {}
    reference_holders: dict[str, list[int]] = {}
    ancestors_by_token: dict[str, list[int]] = {}
    by_body: dict[str, list[int]] = {}
    for index, node in enumerate(nodes):
        _bucket(by_message_id, node.message_id, index)
        _bucket(reply_children, node.in_reply_to, index)
        _bucket(by_body, node.normalized_body_sha256, index)
        references = sorted({token for token in node.references if token})
        for token in references:
            _bucket(reference_holders, token, index)
        ancestors = sorted(set(references) | ({node.in_reply_to} if node.in_reply_to else set()))
        for token in ancestors:
            _bucket(ancestors_by_token, token, index)
    return _EmailKeys(
        by_message_id=by_message_id,
        reply_children=reply_children,
        reference_holders=reference_holders,
        ancestors_by_token=ancestors_by_token,
        by_body=by_body,
    )


def _symmetric_classes(kind: str, buckets: Mapping[str, list[int]]) -> list[_EdgeClass]:
    """Classes whose members all share one token (collision, sibling, template, attachment)."""
    classes: list[_EdgeClass] = []
    for members in buckets.values():
        distinct = tuple(sorted(set(members)))
        if len(distinct) >= 2:
            classes.append(_EdgeClass(kind, _pair_count(len(distinct)), distinct))
    return classes


def _directed_classes(
    kind: str, sources: Mapping[str, list[int]], targets: Mapping[str, list[int]]
) -> list[_EdgeClass]:
    """Classes joining referrers to the email they name (reply, reference); self-pairs excluded."""
    classes: list[_EdgeClass] = []
    for token, source_indices in sources.items():
        target_indices = targets.get(token)
        if not target_indices:
            continue
        source_set, target_set = set(source_indices), set(target_indices)
        count = len(source_set) * len(target_set) - len(source_set & target_set)
        if count <= 0:
            continue
        classes.append(_EdgeClass(kind, count, tuple(sorted(source_set | target_set))))
    return classes


def _is_inline_image(carrier: AttachmentCarrier) -> bool:
    """The one property-based attachment exclusion of §7: an inline image never joins.

    `content_type` is typed `str`, but the corpus column is nullable, so a missing value is
    coalesced here rather than crashing the whole run on one row.
    """
    return carrier.is_inline and (carrier.content_type or "").lower().startswith("image/")


def _index_carriers(
    attachments: Sequence[AttachmentCarrier],
    index_of: Mapping[UUID, int],
    designated_boilerplate: frozenset[str],
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Bucket carriers by content hash: all of them (review list) and the joining ones (edges)."""
    carriers_all: dict[str, set[int]] = {}
    carriers_joining: dict[str, set[int]] = {}
    for carrier in attachments:
        index = index_of.get(carrier.email_id)
        if index is None:
            continue
        carriers_all.setdefault(carrier.content_hash, set()).add(index)
        if carrier.content_hash in designated_boilerplate or _is_inline_image(carrier):
            continue
        carriers_joining.setdefault(carrier.content_hash, set()).add(index)
    return carriers_all, carriers_joining


def _all_edge_classes(
    keys: _EmailKeys, carriers_joining: Mapping[str, set[int]]
) -> list[_EdgeClass]:
    """Every key class of every edge kind, in the frozen kind order of `EDGE_KINDS`."""
    attachment_buckets = {
        content_hash: sorted(indices) for content_hash, indices in carriers_joining.items()
    }
    return [
        *_directed_classes("reply", keys.reply_children, keys.by_message_id),
        *_directed_classes("reference", keys.reference_holders, keys.by_message_id),
        *_symmetric_classes("collision", keys.by_message_id),
        *_symmetric_classes("sibling", keys.ancestors_by_token),
        *_symmetric_classes("attachment", attachment_buckets),
        *_symmetric_classes("template", keys.by_body),
    ]


def _group_id(email_ids: Iterable[UUID]) -> str:
    """§16.3: sha256 over the canonical `str(uuid)` members, sorted bytewise, joined by `\\n`."""
    joined = "\n".join(sorted(str(email_id) for email_id in email_ids))
    return sha256(joined.encode("utf-8")).hexdigest()


def _build_groups(
    nodes: Sequence[EmailNode],
    parent: list[int],
    classes: Sequence[_EdgeClass],
    carriers_joining: Mapping[str, set[int]],
) -> tuple[LeakageGroup, ...]:
    """Materialize the components with their per-kind edge counts and joining attachment hashes."""
    members_by_root: dict[int, list[UUID]] = {}
    for index, node in enumerate(nodes):
        members_by_root.setdefault(_find(parent, index), []).append(node.email_id)
    counts_by_root = {root: dict.fromkeys(EDGE_KINDS, 0) for root in members_by_root}
    for edge_class in classes:
        counts_by_root[_find(parent, edge_class.members[0])][edge_class.kind] += edge_class.count
    hashes_by_root: dict[int, list[str]] = {}
    for content_hash, indices in carriers_joining.items():
        if len(indices) >= 2:
            root = _find(parent, min(indices))
            hashes_by_root.setdefault(root, []).append(content_hash)
    groups = [
        LeakageGroup(
            group_id=_group_id(email_ids),
            email_ids=tuple(sorted(email_ids)),
            size=len(email_ids),
            edge_counts=dict(counts_by_root[root]),
            attachment_hashes=tuple(sorted(hashes_by_root.get(root, ()))),
        )
        for root, email_ids in members_by_root.items()
    ]
    return tuple(sorted(groups, key=lambda group: group.group_id))


def _edges_of_kind(classes: Sequence[_EdgeClass], kind: str) -> int:
    """Total pairs contributed by one edge kind across every key class."""
    return sum(edge_class.count for edge_class in classes if edge_class.kind == kind)


def group_rows(
    emails: Sequence[EmailNode],
    attachments: Sequence[AttachmentCarrier],
    *,
    designated_boilerplate: frozenset[str] = frozenset(),
) -> LeakageResult:
    """Partition `emails` into leakage groups per contract §7 (pure, order independent).

    Contract:
        Joins a pair of emails when any §7 edge holds — reply, reference, message-id collision,
        shared ancestor token (§16.11 `ancestors = {in_reply_to} ∪ references`), a shared
        attachment content hash, or an identical `normalized_body_sha256`. Attachment edges are
        suppressed for a hash in `designated_boilerplate` and for inline-image carrier rows; no
        other property excludes an edge. Returns every input email in exactly one group, groups
        sorted by `group_id`, plus the aggregates of §1.4. `input_corpus_digest` is `None` here:
        only the database-backed caller knows which corpus snapshot the rows came from.

    Edge cases:
        Null or empty `message_id`, `in_reply_to`, references tokens and body digests never
        match anything, so they never join (two null message ids are not a collision). A carrier
        naming an email outside `emails` is ignored. Duplicate carrier rows for one email and
        hash count once. An empty input yields an empty result.
    """
    nodes = tuple(emails)
    index_of = {node.email_id: index for index, node in enumerate(nodes)}
    keys = _index_email_keys(nodes)
    carriers_all, carriers_joining = _index_carriers(attachments, index_of, designated_boilerplate)
    classes = _all_edge_classes(keys, carriers_joining)

    parent = list(range(len(nodes)))
    for edge_class in classes:
        _union_all(parent, edge_class.members)
    groups = _build_groups(nodes, parent, classes, carriers_joining)

    sizes = sorted((group.size for group in groups), reverse=True)
    histogram: dict[int, int] = {}
    for size in sizes:
        histogram[size] = histogram.get(size, 0) + 1
    review = {
        content_hash: len(indices)
        for content_hash, indices in sorted(carriers_all.items())
        if len(indices) > UBIQUITY_REVIEW_TRIGGER
    }
    return LeakageResult(
        version=LEAK_GROUPS_VERSION,
        groups=groups,
        singleton_count=sum(1 for group in groups if group.size == 1),
        size_histogram=dict(sorted(histogram.items())),
        largest_sizes=tuple(sizes[:LARGEST_SIZES_REPORTED]),
        review_trigger_hashes=review,
        designated_boilerplate_applied=tuple(sorted(designated_boilerplate & set(carriers_all))),
        collision_edges=_edges_of_kind(classes, "collision"),
        sibling_edges=_edges_of_kind(classes, "sibling"),
        template_edges=_edges_of_kind(classes, "template"),
        input_corpus_digest=None,
        constants={
            "ubiquity_review_trigger": UBIQUITY_REVIEW_TRIGGER,
            "largest_sizes_reported": LARGEST_SIZES_REPORTED,
        },
    )


# ── wave 2: the corpus-backed entry point ─────────────────────────────────────────────────


async def compute_leakage_groups(
    conn: AsyncSession, org_id: UUID, *, designated_boilerplate: frozenset[str] = frozenset()
) -> LeakageResult:
    """Group one org's emails from the R6 snapshot `conn` and stamp the corpus digest (§7).

    Args:
        conn: The caller's `REPEATABLE READ` + `READ ONLY` snapshot session (contract R6) —
            the roster, the bodies' digests and the corpus identity all come from it, so the
            partition and the digest describe the same instant.
        org_id: The tenant whose emails are grouped.
        designated_boilerplate: The frozen content hashes the founder has designated boilerplate
            (stage B); their attachment edges are suppressed, as in `group_rows`.

    Contract:
        Reads `email_message` and `email_attachment` for `org_id`, derives each
        `normalized_body_sha256` with EVID_NORM_V1, delegates the partition to `group_rows`, and
        records the snapshot's `CORPUS_DIGEST_V1` digest as `input_corpus_digest` so the emitted
        report names the corpus it describes. Adds no index and writes nothing.

    Edge cases:
        An org with no emails yields an empty partition that still carries the corpus digest.
        Attachments whose `content_hash` is NULL never join and never reach the review list.
    """
    emails = await leakage_db.load_email_nodes(conn, org_id, EmailNode)
    attachments = await leakage_db.load_attachment_carriers(conn, org_id, AttachmentCarrier)
    result = group_rows(emails, attachments, designated_boilerplate=designated_boilerplate)
    identity = await corpus_identity.corpus_digest(conn, org_id)
    return replace(result, input_corpus_digest=identity.corpus_digest)
