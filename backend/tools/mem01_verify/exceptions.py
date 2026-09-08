"""
Role: The single exception hierarchy of the MEM-01 Stage-A instruments — one `Mem01Error` base
      and the eighteen named subtypes of contract §1.3 (plus `ValidationGuardError`, added by
      determination §16.16(h)), so every public instrument surface can refuse with a typed
      error instead of a bare `ValueError`.
Used by: every module of `tools.mem01_verify` and the sealed oracle under
      `backend/tests/tools/mem01_verify/`.
Depends on: nothing inside the project (stdlib only) — this module is the root of the package's
      dependency graph and must stay import-free so any module may raise from it.
Key invariants:
  - Every class defined here is a direct subclass of `Mem01Error`, which is a subclass of
    `Exception`; callers may catch `Mem01Error` to catch every instrument refusal.
  - The set of names is closed by contract §1.3 and §16.16(h): this module is append-only and
    owned by one builder per wave; a new failure mode reuses the closest existing class
    rather than inventing an untyped error.
  - Messages never carry personal data or gold-record ids (rule R5 / security.md): they state
    counts, paths inside the release, hashes and field names only.
"""

from __future__ import annotations

from collections.abc import Mapping


class Mem01Error(Exception):
    """Base class for every refusal raised by a MEM-01 Stage-A instrument."""


class ReleaseLockError(Mem01Error):
    """The release lock, manifest or a manifest-declared file failed verification (§4.3)."""


class RosterMismatchError(Mem01Error):
    """A gold roster differs from the manifest, or a data line is malformed (§16.10/§16.14).

    Args:
        message: Human-readable statement of the counts. Never names a record id (R5).
        counts: Per set, the counters `expected, present, missing, duplicate, unexpected,
            malformed`. `None` when the failure is not expressible as per-set counts.
    """

    def __init__(
        self,
        message: str,
        *,
        counts: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        super().__init__(message)
        self.counts: Mapping[str, Mapping[str, int]] = counts if counts is not None else {}


class RunnerHashMismatchError(Mem01Error):
    """`runner_sha256()` differs from the value a frozen release's manifest froze (R7)."""


class Utf8SelfTestError(Mem01Error):
    """The UTF-8 self-test of §3.9 could not prove the output streams are UTF-8."""


class HiddenBudgetExhaustedError(Mem01Error):
    """A selected hidden split has spent its effective budget; no hidden file was opened."""


class HiddenBudgetLedgerError(Mem01Error):
    """The hidden-budget ledger is missing, unreadable or internally inconsistent (§3.6)."""


class ValidationRefusedError(Mem01Error):
    """The `--validation` preconditions of §3.7 do not hold; the holdout stays unread."""


class ValidationGuardError(Mem01Error):
    """The validation guard could not seal an aborted attempt's report directory (§3.7/§16.16h).

    Raised when the `<run_id>.sealed` target already exists or the attempt directory is absent
    — the guard never overwrites an earlier sealed attempt.
    """


class IntegrityViolationError(Mem01Error):
    """An append-only artifact is torn, rewritten, or an event envelope is malformed (§16.12)."""


class ProbeDatabaseError(Mem01Error):
    """A probe database could not be created, claimed, reused or dropped (§12/§16.4)."""


class CriteriaError(Mem01Error):
    """The criteria annex violates the schema or the consistency rules of §4.5."""


class FixtureError(Mem01Error):
    """A public fixture battery is inconsistent with its declared contract (§10)."""


class VerdictFormatError(Mem01Error):
    """A verdict line deviates from the grammar of §3.8, in either direction."""


class ResultBlockError(Mem01Error):
    """The machine block is absent, duplicated, unparsable or schema-invalid (§3.3/§3.4)."""


class NormalizationError(Mem01Error):
    """EVID_NORM_V1 could not normalize or map an offset back to the original text (§6)."""


class CensusError(Mem01Error):
    """The versioned census could not be taken or serialized (§9)."""


class SnapshotError(Mem01Error):
    """The text snapshot could not be emitted, digested or compared (§5)."""


class RunRefusedError(Mem01Error):
    """The runner refused this invocation before it could score anything (§3.2/§16.13).

    Raised for an unknown `--gates` name, a hidden run no H gate can score, and a partial run
    asking for an acceptance path — refusals of the invocation itself, never of an artifact.
    """
