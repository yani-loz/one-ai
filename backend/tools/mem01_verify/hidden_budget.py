"""
Role: The cross-release hidden-test budget of contract §3.6 — ONE append-only ledger at the gold
      root, keyed per test split by `split_digest`, that reserves a unit BEFORE any hidden file is
      opened, refuses a selected split that has spent its effective limit, repeats a completed
      `(code_hash, config_hash, split_digests)` pair for free, and records every attempt's outcome
      (failed and cancelled stay charged).
Used by: `runner_steps` (step-6 reserve/counters), `verify_step1` (`record_outcome`), `.release`.
Depends on: `tools.mem01_verify.audit_file` (the §16.12 envelope writer / reader), `.exceptions`,
      `.hashing`, `.hidden_budget_replay` (the pure replay selection, which also owns the
      `hidden_reservation` / `hidden_outcome` event names re-exported here), `.statuses`
      (`H_SPLIT_GATES`), `.verdict` (`HiddenCounters`).
Key invariants:
  - Counters are keyed by SPLIT DIGEST, never by release or lock, so a byte-identical
    `test/<set>` in a superseding release inherits the units already spent against it.
  - Nothing is cached: every call re-reads the ledger, and the read → free-repeat → exhaustion
    → append sequence runs inside one exclusive critical section (an in-process lock plus an OS
    lock on a sibling `.lock` file), so the limit can never be overrun by a concurrent reserver.
  - An effective limit only ever rises: a `budget_raise` below it is ignored (§3.6).
  - The events written here carry EXACTLY `type`, `event_id`, `at` plus the keys §16.1 lists,
    stamped by the envelope writer; a completed attempt's protected result is content-addressed
    and persisted under the HIDDEN root the caller supplies as `results_root`
    (`<results_root>/hidden_budget.results/<sha256>.json`), never beside the visible ledger,
    which records only `protected_result_sha256` and `protected_result_path` (§16.17(h)).
  - Only ONE completed result of a pair is ever loaded on a replay (§16.17(h) as amended by
    §16.18(c)): the whole ledger is scanned first and `hidden_budget_replay` names the
    reservation whose deciding `completed` outcome appears LAST in it — completion order, not
    reservation order — so a superseded result is never opened, let alone read.
  - A cached result is replayed only when the recorded digest is 64 lowercase hex AND the cached
    bytes hash to it: a malformed digest names and opens no file at all, and tampered bytes
    refuse the free repeat rather than replaying an attacker's block. That refusal leaves
    `reserve` as a `HiddenBudgetLedgerError` and the RUN ABORTS (exit 2, nothing charged) for
    the founder to inspect; the pair is charged and re-executed only on a later run, once the
    cache has been repaired or removed (§16.17(h)).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from tools.mem01_verify import audit_file
from tools.mem01_verify.exceptions import HiddenBudgetExhaustedError, HiddenBudgetLedgerError
from tools.mem01_verify.hashing import canonical_lines_digest, sha256_bytes
from tools.mem01_verify.hidden_budget_replay import (
    OUTCOME_EVENT,
    RESERVATION_EVENT,
    select_replayable_outcome,
)
from tools.mem01_verify.statuses import H_SPLIT_GATES
from tools.mem01_verify.verdict import HiddenCounters

if os.name == "nt":  # the OS lock below is the only platform-dependent code in the package
    import msvcrt
else:
    import fcntl

HIDDEN_BUDGET_DEFAULT_LIMIT = 20

BUDGET_RAISE_EVENT = "budget_raise"
OUTCOME_VALUES = ("completed", "failed", "cancelled")

_HIDDEN_PREFIX = "hidden/"
_RESULTS_DIRNAME = "hidden_budget.results"
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_LOCK_SUFFIX = ".lock"
_LOCK_POLL_SECONDS = 0.01
_LOCK_TIMEOUT_SECONDS = 30.0

_PROCESS_LOCK = threading.Lock()


@dataclass(frozen=True)
class Reservation:
    """One durable claim on the hidden budget, or the replay of a completed pair (§1.4).

    `counters_before` holds the counters as they stood BEFORE this reservation; `recorded_result`
    is set only on a free repeat — the earlier completed attempt's protected result, whose
    `reservation_id` this reservation then carries.
    """

    reservation_id: str
    lock_sha256: str
    split_digests: Mapping[str, str]
    code_hash: str
    config_hash: str
    run_id: str
    counters_before: HiddenCounters
    recorded_result: Mapping[str, object] | None = None


def split_digest(manifest: Mapping[str, object], set_name: str) -> str:
    """Return the budget key of one hidden TEST split: its manifest entries, digested (§3.6).

    The digest covers the path, sha256 and record count of every manifest file under
    `test/<set>/` (a leading `hidden/` prefix is stripped), bytewise sorted — independent of
    manifest order and of every other set, and different whenever that set's bytes or record
    counts differ.

    An H split the manifest names NO hidden test files for is keyed by the digest of the EMPTY
    per-split record set (§16.16(f)): scorability needs hidden files, so such a split can never
    be reserved — its counter only ever displays 0 and its ledger key is never written — and the
    four-split display must not depend on the holdout being complete.

    Args:
        manifest: The release manifest, whose `files` entries key the digest.
        set_name: One of the four H splits (`statuses.H_SPLIT_GATES`).

    Returns:
        The 64-character lowercase hex budget key of that split.

    Raises:
        HiddenBudgetLedgerError: `set_name` is not an H split, the manifest carries no `files`
            mapping, or an entry under `test/<set_name>/` lacks `sha256` or `records`.
    """
    if set_name not in H_SPLIT_GATES:
        raise HiddenBudgetLedgerError(f"only {H_SPLIT_GATES} are splits; got {set_name!r}")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise HiddenBudgetLedgerError("the manifest carries no 'files' mapping to digest")
    prefix = f"test/{set_name}/"
    lines: list[str] = []
    for path, entry in files.items():
        relative = str(path).removeprefix(_HIDDEN_PREFIX)
        if not relative.startswith(prefix):
            continue
        if not isinstance(entry, Mapping) or "sha256" not in entry or "records" not in entry:
            raise HiddenBudgetLedgerError(
                f"a manifest entry under test/{set_name}/ lacks sha256 or records"
            )
        lines.append(f"{relative}\t{entry['sha256']}\t{entry['records']}")
    return canonical_lines_digest(lines)


def _lock_once(descriptor: int, *, unlock: bool = False) -> bool:
    """Take or release one non-blocking exclusive OS lock; `False` when another holder has it."""
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextmanager
def _exclusive(ledger_path: Path) -> Iterator[None]:
    """Serialize the read-check-append sequence against other threads AND other processes."""
    lock_path = ledger_path.with_name(ledger_path.name + _LOCK_SUFFIX)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    timeout_message = f"the lock {lock_path.name} stayed held; refusing to reserve"
    with _PROCESS_LOCK:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            while not _lock_once(descriptor):
                if time.monotonic() >= deadline:
                    raise HiddenBudgetLedgerError(timeout_message)
                time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                _lock_once(descriptor, unlock=True)
        finally:
            os.close(descriptor)


def _split_stats(events: list[dict], digest: str) -> tuple[int, int]:
    """One pass over the ledger for one split digest: units spent, and the effective limit.

    Spent counts every `hidden_reservation` naming the digest, across locks and releases; the
    limit starts at `HIDDEN_BUDGET_DEFAULT_LIMIT` and only rises, with a `budget_raise` for that
    digest (§3.6). A raise whose `new_limit` is not an integer raises `HiddenBudgetLedgerError`.
    """
    spent, limit = 0, HIDDEN_BUDGET_DEFAULT_LIMIT
    for event in events:
        event_type = event.get("type")
        if event_type == RESERVATION_EVENT:
            digests = event.get("split_digests")
            spent += 1 if isinstance(digests, Mapping) and digest in set(digests.values()) else 0
        elif event_type == BUDGET_RAISE_EVENT and event.get("split_digest") == digest:
            new_limit = event.get("new_limit")
            if not isinstance(new_limit, int) or isinstance(new_limit, bool):
                raise HiddenBudgetLedgerError("a budget_raise carries a non-integer new_limit")
            limit = max(limit, new_limit)
    return spent, limit


class HiddenBudget:
    """The append-only hidden-test budget ledger of §3.6, read fresh on every operation.

    `ledger_path` is `<gold root>/hidden_budget.jsonl`, one ledger for the whole gold root.
    `create_if_missing` is `True` only for `release cut --draft`, which lays the empty ledger
    down; the runner constructs with the default, so a missing required ledger raises
    `HiddenBudgetLedgerError` rather than opening a silently fresh budget (§16.2).
    `results_root` is the HIDDEN root under which completed protected results are cached
    (§16.17(h)): the runner passes the hidden root it has already checked at step 6, and
    `release cut --draft` passes nothing, so a draft budget can lay the ledger down but can
    never record a completed result. The cache directory `hidden_budget.results/` is created
    under that root, parents included, on the first stored result.
    """

    def __init__(
        self,
        ledger_path: Path,
        *,
        create_if_missing: bool = False,
        results_root: Path | None = None,
    ) -> None:
        self._ledger_path = Path(ledger_path)
        self._results_root = Path(results_root) if results_root is not None else None
        if self._ledger_path.exists():
            return
        if not create_if_missing:
            raise HiddenBudgetLedgerError(f"the ledger {self._ledger_path.name} is missing")
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._ledger_path, "ab"):  # never a truncating open
            pass

    def counters(
        self, split_digests: Mapping[str, str], *, lock_sha256: str | None = None
    ) -> HiddenCounters:
        """Return the cumulative counters for `split_digests` (SET → digest), read from disk.

        A SET without a digest counts as zero; `limit` is the effective limit of the selected
        split attaining the maximum; `lock_sha256`, when given, fills `invocations_under_lock`
        (§16.14). Raises `HiddenBudgetLedgerError` for a SET outside `QS, NF, LANG, RET` or an
        unreadable ledger, `IntegrityViolationError` for a torn last line.
        """
        return self._counters(self._read_events(), split_digests, lock_sha256)

    def reserve(
        self,
        *,
        lock_sha256: str,
        split_digests: Mapping[str, str],
        code_hash: str,
        config_hash: str,
        run_id: str,
    ) -> Reservation:
        """Charge one unit against every selected split, durably, BEFORE any hidden file opens.

        A pair whose latest outcome is `completed` repeats for free (the recorded protected
        result comes back, nothing is appended); one whose latest outcome was `failed` or
        `cancelled` is charged again. `split_digests` maps each SET this run scores to its
        `split_digest`. Raises `HiddenBudgetExhaustedError` — message the literal §3.6 line
        `HIDDEN BUDGET EXHAUSTED <n>/<limit>` — when a selected split is at its effective limit,
        or `HiddenBudgetLedgerError` when the ledger is unreadable or inconsistent.
        """
        with _exclusive(self._ledger_path):
            events = self._read_events()
            before = self._counters(events, split_digests, lock_sha256)
            repeat = self._free_repeat(events, code_hash, config_hash, split_digests)
            if repeat is not None:
                reservation_id, recorded = repeat
            else:
                self._refuse_when_exhausted(events, split_digests)
                reservation_id, recorded = str(uuid4()), None
                audit_file.append_event(
                    self._ledger_path,
                    {
                        "type": RESERVATION_EVENT,
                        "reservation_id": reservation_id,
                        "lock": lock_sha256,
                        "split_digests": dict(split_digests),
                        "code_hash": code_hash,
                        "config_hash": config_hash,
                        "run_id": run_id,
                    },
                )
        return Reservation(
            reservation_id=reservation_id,
            lock_sha256=lock_sha256,
            split_digests=dict(split_digests),
            code_hash=code_hash,
            config_hash=config_hash,
            run_id=run_id,
            counters_before=before,
            recorded_result=recorded,
        )

    def record_outcome(
        self,
        reservation: Reservation,
        *,
        outcome: Literal["completed", "failed", "cancelled"],
        protected_result: Mapping[str, object] | None,
        protected_result_path: str | None = None,
    ) -> None:
        """Close a reservation with its outcome, so a repeat is free or charged again (§16.1).

        A `completed` outcome must carry the full machine block as `protected_result`; it is
        persisted under `results_root` so a free repeat can replay it, and `protected_result_path`
        (report-root-relative, §16.13/§16.14) is recorded verbatim beside its sha256. `failed`
        and `cancelled` stay charged and record `null` for both fields. Raises
        `HiddenBudgetLedgerError` on an unknown outcome, a completed one without a result, or a
        completed one carrying a result on a budget built without `results_root` — refused
        BEFORE any event is appended, so nothing lands beside the visible ledger (§16.17(h)).
        """
        if outcome not in OUTCOME_VALUES:
            raise HiddenBudgetLedgerError(
                f"unknown hidden outcome {outcome!r}; expected one of {OUTCOME_VALUES}"
            )
        digest: str | None = None
        recorded_path: str | None = None
        with _exclusive(self._ledger_path):
            if outcome == "completed":
                if protected_result is None:
                    raise HiddenBudgetLedgerError("a completed attempt must record a result")
                digest = self._store_result(protected_result)
                recorded_path = protected_result_path
            audit_file.append_event(
                self._ledger_path,
                {
                    "type": OUTCOME_EVENT,
                    "reservation_id": reservation.reservation_id,
                    "outcome": outcome,
                    "protected_result_sha256": digest,
                    "protected_result_path": recorded_path,
                },
            )

    # ── internals ─────────────────────────────────────────────────────────────────────────

    def _read_events(self) -> list[dict]:
        """Read the whole ledger; a file that vanished mid-run is an error, never an empty one."""
        try:
            if not self._ledger_path.exists():
                raise OSError("the ledger disappeared mid-run")
            return audit_file.read_events(self._ledger_path)
        except OSError as exc:
            raise HiddenBudgetLedgerError(f"the ledger is unreadable: {exc}") from exc

    def _results_directory(self) -> Path:
        """The cache directory under the hidden root; a rootless budget has none (§16.17(h))."""
        if self._results_root is None:
            raise HiddenBudgetLedgerError(
                "this budget carries no results_root; no protected result can be cached"
            )
        return self._results_root / _RESULTS_DIRNAME

    def _result_file(self, digest: str) -> Path:
        """The content-addressed protected-result file under `<results_root>/`."""
        return self._results_directory() / f"{digest}.json"

    def _store_result(self, protected_result: Mapping[str, object]) -> str:
        """Persist a protected result under its own digest and return that digest."""
        payload = json.dumps(
            dict(protected_result), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest = sha256_bytes(payload)
        target = self._result_file(digest)  # raises before anything is written when rootless
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
        return digest

    def _load_result(self, digest: str) -> Mapping[str, object]:
        """Replay a stored protected result, refusing anything but its own verified bytes.

        The recorded digest is checked against `[0-9a-f]{64}` BEFORE any file is named or
        opened, so a traversal-shaped digest reaches no path at all; the cached bytes are then
        required to hash to that digest before they are parsed. Every refusal raises
        `HiddenBudgetLedgerError` and names at most the digest's first 12 characters (R5).
        """
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise HiddenBudgetLedgerError(
                "a recorded protected-result digest is not 64 lowercase hex; no free repeat"
            )
        try:
            raw = self._result_file(digest).read_bytes()
        except OSError as exc:
            raise HiddenBudgetLedgerError(
                f"the protected result {digest[:12]} is unreadable "
                f"({type(exc).__name__}); no free repeat"
            ) from exc
        if sha256_bytes(raw) != digest:
            raise HiddenBudgetLedgerError(
                f"the protected result {digest[:12]} does not hash to its recorded digest; "
                "no free repeat"
            )
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HiddenBudgetLedgerError(
                f"the protected result {digest[:12]} is unparsable "
                f"({type(exc).__name__}); no free repeat"
            ) from exc
        if not isinstance(loaded, dict):
            raise HiddenBudgetLedgerError(f"protected result {digest[:12]} is not a JSON object")
        return loaded

    def _counters(
        self, events: list[dict], split_digests: Mapping[str, str], lock_sha256: str | None
    ) -> HiddenCounters:
        """Compute the cumulative per-split counters and the effective limit of the maximum."""
        unknown = sorted(name for name in split_digests if name not in H_SPLIT_GATES)
        if unknown:
            raise HiddenBudgetLedgerError(f"only {H_SPLIT_GATES} are splits; got {unknown}")
        spent = dict.fromkeys(H_SPLIT_GATES, 0)
        limits = dict.fromkeys(H_SPLIT_GATES, HIDDEN_BUDGET_DEFAULT_LIMIT)
        for name, digest in split_digests.items():
            spent[name], limits[name] = _split_stats(events, digest)
        total = max(spent.values())
        selected = [name for name in H_SPLIT_GATES if name in split_digests]
        at_maximum = [name for name in selected if spent[name] == total]
        under_lock = sum(
            1
            for event in events
            if event.get("type") == RESERVATION_EVENT and event.get("lock") == lock_sha256
        )
        return HiddenCounters(
            total=total,
            limit=limits[at_maximum[0]] if at_maximum else HIDDEN_BUDGET_DEFAULT_LIMIT,
            by_split=dict(spent),
            invocations_under_lock=under_lock if lock_sha256 is not None else 0,
        )

    def _refuse_when_exhausted(self, events: list[dict], split_digests: Mapping[str, str]) -> None:
        """Raise before any hidden file is opened when a selected split is at its limit (§3.6)."""
        for name in H_SPLIT_GATES:
            if name not in split_digests:
                continue
            spent, limit = _split_stats(events, split_digests[name])
            if spent >= limit:
                raise HiddenBudgetExhaustedError(f"HIDDEN BUDGET EXHAUSTED {spent}/{limit}")

    def _free_repeat(
        self,
        events: list[dict],
        code_hash: str,
        config_hash: str,
        split_digests: Mapping[str, str],
    ) -> tuple[str, Mapping[str, object]] | None:
        """Return the replayed attempt's id and result for this pair, or `None` to charge.

        `hidden_budget_replay.select_replayable_outcome` scans the whole ledger and names the
        pair's reservation whose deciding `completed` outcome appears LAST in it — completion
        order (§16.18(c)) — so exactly one protected result is loaded here and every superseded
        one stays on disk unopened. A completed outcome without a digest raises there.
        """
        selected = select_replayable_outcome(
            events, code_hash=code_hash, config_hash=config_hash, split_digests=split_digests
        )
        if selected is None:
            return None
        reservation_id, digest = selected
        return reservation_id, self._load_result(digest)
