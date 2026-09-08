"""
Role: Roster verification of contract §3.2 step 4 / §16.10 / §16.13 — every `gold_id` the
      manifest lists for a split exists exactly once in that split's data files, and no record in
      those files is absent from the manifest. Counts alone are insufficient, so the check is by
      identity; the refusal reports COUNTS ONLY and never a record id (R5).
Used by: tools.mem01_verify.verify_step1 (step 4 for the optimization split, step 7 for the
      selected hidden split), .release, and the sealed oracle modules
      tests/tools/mem01_verify/test_roster.py and test_roster_hidden_subset.py.
Depends on: tools.mem01_verify.lock (`ReleaseInfo`), .exceptions (`RosterMismatchError`); stdlib.
Key invariants:
  - Only the field `gold_id` is read from a data line; every other field is opaque to Stage A
    (§16.10). A missing, empty, null or non-string `gold_id`, a non-JSON / non-object line, or a
    line that is not valid UTF-8, is `malformed` and raises `RosterMismatchError` (§16.14). The
    UTF-8 check is PER LINE and strict (§16.17(k)): the file is split on `0x0A` — a byte that
    never occurs inside a multi-byte UTF-8 sequence — so one undecodable line is counted and
    skipped while every valid record around it still counts as `present`.
  - `duplicate` is the SUM of both sides of the comparison (§16.17(k)): a gold id the manifest
    lists twice counts one, a gold id present twice in the data files counts one, and both
    together count two.
  - A SET whose manifest entry is the corpus form `{by_digest, roster_counts}` needs no data
    file: its counters are derived from the declared roster counts (§16.13).
  - `RosterMismatchError.counts` carries, per set, `expected, present, missing, duplicate,
    unexpected, malformed`; the message states those numbers and never an id.
  - The visible split reads `<release>/data/optimization/<SET>/`; a hidden split reads
    `<hidden root>/releases/<name>/<split>/<SET>/` — never both in one call.
  - `sets` narrows a HIDDEN split to the named SETs (§16.16g): every other SET's hidden
    file is neither opened, read nor parsed, and carries no counters in the report. The
    argument is ignored on the visible `optimization` split, which opens nothing hidden.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.mem01_verify.exceptions import RosterMismatchError
from tools.mem01_verify.lock import RELEASES_DIRNAME, ReleaseInfo

COUNTER_NAMES = ("expected", "present", "missing", "duplicate", "unexpected", "malformed")
DATA_DIRNAME = "data"
OPTIMIZATION_SPLIT = "optimization"
DATA_SUFFIX = "*.jsonl"
GOLD_ID_FIELD = "gold_id"
BY_DIGEST_FIELD = "by_digest"
ROSTER_COUNTS_FIELD = "roster_counts"


@dataclass(frozen=True)
class RosterReport:
    """Per-set roster counters and the overall verdict (§1.4)."""

    sets: Mapping[str, Mapping[str, int]]
    ok: bool


def _zero_counters() -> dict[str, int]:
    """A counter block with every §16.14 counter at zero."""
    return dict.fromkeys(COUNTER_NAMES, 0)


def _read_gold_ids(directory: Path) -> tuple[list[str], int]:
    """Return `(gold ids in file order, malformed line count)` for one set's data directory.

    Every `*.jsonl` file under `directory` is read in sorted path order; blank lines are ignored.
    A line that is not a JSON object, or whose `gold_id` is absent, null, non-string or empty, is
    counted as malformed and contributes no id. Each line's bytes are decoded STRICTLY, one line
    at a time: a line that is not valid UTF-8 is counted `malformed` and skipped, and the valid
    lines around it are still read (§16.17(k)) — an undecodable line is a roster MISMATCH
    reported by count (§16.14), never an untyped `UnicodeDecodeError` out of the instrument.
    """
    gold_ids: list[str] = []
    malformed = 0
    if not directory.is_dir():
        return gold_ids, malformed
    for path in sorted(directory.rglob(DATA_SUFFIX)):
        if not path.is_file():
            continue
        for raw in path.read_bytes().split(b"\n"):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed += 1
                continue
            gold_id = record.get(GOLD_ID_FIELD) if isinstance(record, dict) else None
            if isinstance(gold_id, str) and gold_id:
                gold_ids.append(gold_id)
            else:
                malformed += 1
    return gold_ids, malformed


def _expected_ids(entry: Mapping[str, object], split: str) -> list[str]:
    """Return the sorted `gold_id` list the manifest declares for `split` (§16.13)."""
    listed = entry.get(split, [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise RosterMismatchError(f"manifest records[...][{split!r}] is not a list of gold ids")
    return [str(item) for item in listed]


def _corpus_counters(entry: Mapping[str, object]) -> dict[str, int]:
    """Counters for a corpus roster declared by digest — expected == present, nothing missing."""
    counts = entry.get(ROSTER_COUNTS_FIELD)
    if not isinstance(counts, dict) or not all(isinstance(v, int) for v in counts.values()):
        raise RosterMismatchError("a corpus roster carries no usable 'roster_counts'")
    total = sum(int(value) for value in counts.values())
    counters = _zero_counters()
    counters["expected"] = total
    counters["present"] = total
    return counters


def _split_directory(
    release: ReleaseInfo, split: str, hidden_root: Path | None, set_name: str
) -> Path:
    """Where one set's data files for `split` live — under the release, or under the hidden root."""
    if split == OPTIMIZATION_SPLIT:
        return release.path / DATA_DIRNAME / OPTIMIZATION_SPLIT / set_name
    if hidden_root is None:
        raise RosterMismatchError(f"split {split!r} needs a hidden root and none was given")
    return hidden_root / RELEASES_DIRNAME / release.name / split / set_name


def _count_set(expected: Sequence[str], present: Sequence[str], malformed: int) -> dict[str, int]:
    """Compare one set's expected ids against the ids read from its data files."""
    expected_set = set(expected)
    present_set = set(present)
    counters = _zero_counters()
    counters["expected"] = len(expected)
    counters["present"] = len(present)
    counters["missing"] = len(expected_set - present_set)
    counters["duplicate"] = (len(present) - len(present_set)) + (len(expected) - len(expected_set))
    counters["unexpected"] = len(present_set - expected_set)
    counters["malformed"] = malformed
    return counters


def _is_mismatch(counters: Mapping[str, int]) -> bool:
    """True when any of the four defect counters (or a duplicate expected list) is non-zero."""
    return any(counters[name] for name in ("missing", "duplicate", "unexpected", "malformed"))


def _mismatch_message(counters: Mapping[str, Mapping[str, int]]) -> str:
    """State the offending sets' counters — set names and numbers only, never a record id."""
    offenders = [name for name, block in sorted(counters.items()) if _is_mismatch(block)]
    details = "; ".join(
        f"{name} " + " ".join(f"{counter}={counters[name][counter]}" for counter in COUNTER_NAMES)
        for name in offenders
    )
    return f"roster mismatch on {len(offenders)} set(s): {details}"


def _selected_sets(split: str, sets: Sequence[str] | None) -> frozenset[str] | None:
    """The SET names a hidden call restricts itself to, or `None` to verify every SET (§16.16g)."""
    if sets is None or split == OPTIMIZATION_SPLIT:
        return None
    return frozenset(str(name) for name in sets)


def verify_roster(
    release: ReleaseInfo,
    *,
    split: Literal["optimization", "test", "validation"],
    hidden_root: Path | None,
    sets: Sequence[str] | None = None,
) -> RosterReport:
    """Verify the manifest's roster for one split against that split's data files (§3.2 step 4).

    For every SET the manifest's `records` names, the declared `gold_id` list for `split` is
    compared by identity against the ids read from the split's data files; a SET declared by
    corpus digest is accepted on its declared roster counts. Nothing outside the requested
    split is opened.

    Args:
        release: the `ReleaseInfo` stage 1 of the lock returned.
        split: `optimization` (visible), or `test` / `validation` (under the hidden root).
        hidden_root: the hidden root, required for a hidden split and ignored otherwise.
        sets: on a HIDDEN split, the only SET names to verify — the runner passes exactly the
            SETs this run reserved, and no other SET's hidden file is opened, read or parsed
            (§16.16g). `None` (the default) verifies every SET the manifest names; the
            argument is ignored on the `optimization` split.

    Returns:
        The `RosterReport` with `ok=True` — a mismatch raises instead of returning.

    Raises:
        RosterMismatchError: a record is missing, duplicated or unexpected, a data line is
            malformed, or the manifest's `records` block is unusable.
    """
    records = release.manifest.get("records")
    if not isinstance(records, dict):
        raise RosterMismatchError("release manifest carries no usable 'records' block")
    selected = _selected_sets(split, sets)
    counters: dict[str, Mapping[str, int]] = {}
    for set_name, entry in sorted(records.items()):
        if selected is not None and str(set_name) not in selected:
            continue
        if not isinstance(entry, dict):
            raise RosterMismatchError(f"manifest records[{set_name!r}] is not a JSON object")
        if BY_DIGEST_FIELD in entry:
            counters[str(set_name)] = _corpus_counters(entry)
            continue
        expected = _expected_ids(entry, split)
        present, malformed = _read_gold_ids(
            _split_directory(release, split, hidden_root, str(set_name))
        )
        counters[str(set_name)] = _count_set(expected, present, malformed)
    if any(_is_mismatch(block) for block in counters.values()):
        raise RosterMismatchError(_mismatch_message(counters), counts=counters)
    return RosterReport(sets=counters, ok=True)
