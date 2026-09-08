"""
Role: The release tooling of contract §4 — `python -m tools.mem01_verify.release`. `cut --draft`
      produces or refreshes the §4.1 draft layout under the gold root (criteria, protocol,
      schemas, fixtures, the empty optimization split, the deterministic text snapshot, the
      corpus identity, the empty instrument directories, the release audit journal and the
      gold-root hidden-budget ledger) and writes the §4.2 manifest whose bytes are the lock;
      `instruments --draft` (and the three per-instrument subcommands) runs the census, the
      leakage grouper and the language bootstrap INTO an existing draft so the next `cut`
      re-manifests them (§16.12). `--freeze` is reserved for stage B and is refused here.
Used by: the §13 baseline pair, the Stage A sealed oracle (tests/tools/mem01_verify), and the
      founder when cutting the real draft release.
Depends on: tools.mem01_verify.release_manifest, .criteria, .hashing, .hidden_budget (the
      gold-root ledger is laid down through `HiddenBudget(create_if_missing=True)`, the one
      writer of that file), .exceptions, and — loaded lazily so the freeze refusal never opens
      a database — .db, .lock, .corpus_identity, .snapshot, .census, .leakage, .lang_bootstrap,
      .fixtures.digest.
Key invariants:
  - The cut is READ-ONLY against the configured database: every read runs inside the R6
    snapshot session, and no statement outside it is issued.
  - The cut NEVER runs the census, leakage or language-bootstrap instruments — it creates their
    directories empty (§16.12).
  - Re-cutting an unchanged release yields a byte-identical manifest except `cut_at`, and never
    truncates `audit.jsonl` or the gold-root ledger.
  - Nothing this module prints or writes into the manifest carries personal data: paths,
    hashes, counts and UUIDs only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from tools.mem01_verify import release_manifest as manifest_builder
from tools.mem01_verify.criteria import CriteriaFile, load_criteria
from tools.mem01_verify.exceptions import IntegrityViolationError, Mem01Error, ReleaseLockError
from tools.mem01_verify.hashing import is_bytecode, runner_sha256, sha256_file
from tools.mem01_verify.hidden_budget import HiddenBudget

if TYPE_CHECKING:  # imported for annotations only — the runtime imports stay lazy
    from tools.mem01_verify.corpus_identity import CorpusIdentity
    from tools.mem01_verify.lock import ReleaseInfo
    from tools.mem01_verify.snapshot import SnapshotSummary

DEFAULT_RELEASE_NAME = "step1-gold-v1"
DEFAULT_ORG_ID = UUID("d1500000-0000-0000-0000-000000000001")
GOLD_ROOT_ENV = "MEM01_GOLD_ROOT"
INSTRUMENT_NAMES = ("census", "leakage", "lang_bootstrap")
LAYOUT_DIRS = (
    "schemas",
    "data/optimization",
    "fixtures",
    "snapshots",
    "reports",
    *INSTRUMENT_NAMES,
)
SAMPLE_SEED: dict[str, object] = {
    "state": "unsampled",
    "seed": None,
    "drawn_at": None,
    "strata_cells": [],
    "split_assignment": {},
    "note": "Stage A cuts a draft release before any sample is drawn (contract 4.1).",
}
FREEZE_PRECONDITIONS = (
    "data/optimization is non-empty",
    "the hidden root is present with test/ and validation/ populated at leakage-group level",
    "test_groups_provenance is declared",
    "the budget ledger is present",
    "hidden inputs are audited as separated from every visible artifact (the full-corpus "
    "snapshot under the visible release is draft-only and must not remain visible for records "
    "assigned to hidden splits)",
)
_PACKAGE_DIR = Path(__file__).resolve().parent
_SOURCE_RELEASE_DIR = _PACKAGE_DIR / "release"
_REPO_ROOT = _PACKAGE_DIR.parents[2]


def _ignore_bytecode(directory: str, names: list[str]) -> set[str]:
    """The `shutil.copytree` ignore callable of the cut: it drops bytecode and nothing else.

    `copytree` calls this once per visited directory. The decision is the ONE §16.16(r)/A26
    predicate `hashing.is_bytecode` — no directory takes part — so a non-bytecode file under
    `__pycache__` is copied, while `.pyc`, `.pyo` and CPython's temporary
    `x.cpython-312.pyc.140213` names are not. Bytecode is also the only exclusion
    `release_manifest.is_manifested` applies to a file COPIED IN (its other three name release
    artifacts — the manifest, `audit.jsonl` and `reports/**` — which no source tree holds), so
    every file this callable admits is one the manifest will name.

    Args:
        directory: The directory being copied (`copytree` passes it as a string).
        names: The entry names inside it.

    Returns:
        The subset of `names` that is bytecode — the entries `copytree` skips.
    """
    return {name for name in names if is_bytecode(Path(directory) / name)}


def default_gold_root() -> Path:
    """Return the gold root: `MEM01_GOLD_ROOT` when set, else `<workspace parent>/Benchmarks`."""
    configured = os.environ.get(GOLD_ROOT_ENV)
    if configured:
        return Path(configured)
    return _REPO_ROOT.parent / "Benchmarks" / "_mem01_gold"


def _now_iso() -> str:
    """The current instant as an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _replace_tree(source: Path, target: Path) -> None:
    """Copy `source` onto `target`, dropping bytecode and any file the source no longer has.

    Bytecode is the only thing dropped, and only through `_ignore_bytecode` — so every file that
    lands in the release is one `release_manifest.is_manifested` admits (§16.6 + §16.16(r)), and
    the cut never has to re-decide what bytecode is.
    """
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=_ignore_bytecode)


def _create_layout(release_dir: Path, gold_root: Path) -> None:
    """Create the §4.1 directory skeleton and the append-only journals (never truncating)."""
    for relative in LAYOUT_DIRS:
        (release_dir / relative).mkdir(parents=True, exist_ok=True)
    (release_dir / manifest_builder.AUDIT_NAME).touch(exist_ok=True)
    # The ledger is created through its own owner, which never truncates an existing file, so
    # `cut --draft` is the only writer of the empty ledger and no second creation seam exists.
    HiddenBudget(gold_root / manifest_builder.LEDGER_NAME, create_if_missing=True)


def _copy_release_inputs(release_dir: Path) -> None:
    """Copy the annex, the protocol stub, the schemas and the fixtures package into the release."""
    for name in (manifest_builder.CRITERIA_NAME, manifest_builder.PROTOCOL_NAME):
        shutil.copyfile(_SOURCE_RELEASE_DIR / name, release_dir / name)
    _replace_tree(
        _SOURCE_RELEASE_DIR / manifest_builder.SCHEMAS_DIR,
        release_dir / manifest_builder.SCHEMAS_DIR,
    )
    _replace_tree(_PACKAGE_DIR / "fixtures", release_dir / "fixtures")
    (release_dir / "sample_seed.json").write_text(
        json.dumps(SAMPLE_SEED, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


async def _emit_snapshot_for(
    release_dir: Path, org_id: UUID, conn: object, text_digest: str
) -> SnapshotSummary:
    """Emit the §5.1 text snapshot into `snapshots/<text_digest>/` and return its summary."""
    from tools.mem01_verify import snapshot

    # `emit_snapshot` takes the RELEASE directory and creates `snapshots/<text_digest>/` itself
    # (§16.15); passing the digest folder would double the path and, on Windows, exceed MAX_PATH.
    target = release_dir / "snapshots" / text_digest
    shutil.rmtree(target, ignore_errors=True)
    return await snapshot.emit_snapshot(conn, org_id, release_dir)


def _corpus_section(identity: CorpusIdentity, summary: SnapshotSummary) -> dict[str, object]:
    """Return the §4.2 `corpus` block — identity and counts only, stable across re-cuts."""
    counts = dict(identity.roster_counts)
    return {
        "org_id": str(identity.org_id),
        "emails": counts.get("email_message", 0),
        "attachments": counts.get("email_attachment", 0),
        "corpus_digest": identity.corpus_digest,
        "text_digest": summary.text_digest,
        "snapshot_manifest_sha256": sha256_file(summary.manifest_path),
    }


async def cut_draft_release(gold_root: Path, name: str, org_id: UUID, conn: object) -> ReleaseInfo:
    """Cut or refresh the draft release for `org_id` under `gold_root`; return its ReleaseInfo.

    The cut is idempotent: re-running it over an unchanged gold root rewrites the same bytes
    everywhere except `cut_at`, keeps `audit.jsonl` and the gold-root ledger, and re-manifests
    whatever the instruments wrote since the previous cut (§16.12).

    Args:
        gold_root: The gold root directory (created when absent).
        name: The release name; the release lives at `<gold_root>/releases/<name>/`.
        org_id: The corpus tenant the release identifies.
        conn: An open R6 read-only snapshot session on the configured database.

    Returns:
        The `lock.ReleaseInfo` of the freshly written release, carrying its computed lock.

    Raises:
        IntegrityViolationError: The corpus identity and the snapshot emitter disagree on
            `text_digest`, so the release would not identify one consistent corpus state.
        Mem01Error: Any release, criteria or snapshot failure raised by the modules called.
    """
    from tools.mem01_verify import corpus_identity, lock
    from tools.mem01_verify.fixtures.digest import fixtures_digest

    release_dir = gold_root / "releases" / name
    _create_layout(release_dir, gold_root)
    _copy_release_inputs(release_dir)
    criteria = load_criteria(release_dir / manifest_builder.CRITERIA_NAME)
    identity = await corpus_identity.corpus_digest(conn, org_id)
    summary = await _emit_snapshot_for(release_dir, org_id, conn, identity.text_digest)
    if summary.text_digest != identity.text_digest:
        raise IntegrityViolationError(
            "the snapshot emitter and the corpus identity disagree on text_digest "
            f"({summary.text_digest} vs {identity.text_digest})"
        )
    manifest = manifest_builder.build_manifest(
        release_dir,
        name=name,
        cut_at=_now_iso(),
        runner_digest=runner_sha256(),
        criteria=criteria,
        corpus=_corpus_section(identity, summary),
        roster_counts=identity.roster_counts,
        fixtures_digest=fixtures_digest(),
    )
    manifest_builder.write_manifest(release_dir, manifest)
    return lock.verify_release_visible(release_dir, expect_lock=None)


async def run_instruments(
    release_dir: Path, org_id: UUID, conn: object, selected: Sequence[str], criteria: CriteriaFile
) -> tuple[str, ...]:
    """Run the selected instruments into the release and return the names actually run.

    Args:
        release_dir: An existing draft release directory.
        org_id: The corpus tenant to measure.
        conn: An open R6 read-only snapshot session on the configured database.
        selected: The instrument names to run (a subset of §16.12's three).
        criteria: The loaded annex — its leakage policy names the designated boilerplate.

    Returns:
        The instrument names that ran, in §16.12 order.
    """
    from tools.mem01_verify import census, lang_bootstrap, leakage

    boilerplate = frozenset(
        str(value) for value in criteria.leakage_policy.get("designated_boilerplate_hashes", [])
    )
    ran: list[str] = []
    for instrument in INSTRUMENT_NAMES:
        if instrument not in selected:
            continue
        out_dir = release_dir / instrument
        out_dir.mkdir(parents=True, exist_ok=True)
        if instrument == "census":
            census.write_census(await census.take_census(conn, org_id), out_dir)
        elif instrument == "leakage":
            groups = await leakage.compute_leakage_groups(
                conn, org_id, designated_boilerplate=boilerplate
            )
            leakage.write_leakage(groups, out_dir)
        else:
            await lang_bootstrap.bootstrap_language(conn, org_id, out_dir)
        ran.append(instrument)
    return tuple(ran)


def build_parser() -> argparse.ArgumentParser:
    """Build the `release` CLI: `cut`, `instruments`, and the three per-instrument subcommands."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.mem01_verify.release",
        description="Cut the MEM-01 step-1 draft release and run its instruments (contract 4).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    for subcommand in ("cut", "instruments", *INSTRUMENT_NAMES):
        child = subparsers.add_parser(subcommand)
        child.add_argument(
            "--draft", action="store_true", help="draft release (the only Stage A mode)"
        )
        child.add_argument(
            "--freeze", action="store_true", help="reserved for stage B; refused in Stage A"
        )
        child.add_argument("--gold-root", type=Path, default=None, help="the gold root directory")
        child.add_argument("--name", default=DEFAULT_RELEASE_NAME, help="the release name")
        child.add_argument("--org", type=UUID, default=DEFAULT_ORG_ID, help="the corpus org id")
        if subcommand == "instruments":
            child.add_argument(
                "--only", default=None, help="comma-separated subset of the three instruments"
            )
    return parser


def _selected_instruments(subcommand: str, only: str | None) -> tuple[str, ...]:
    """Resolve which instruments a subcommand runs; ReleaseLockError names an unknown one."""
    if subcommand in INSTRUMENT_NAMES:
        return (subcommand,)
    if not only:
        return INSTRUMENT_NAMES
    requested = [name.strip() for name in only.split(",") if name.strip()]
    unknown = sorted({name for name in requested if name not in INSTRUMENT_NAMES})
    if unknown:
        raise ReleaseLockError(f"unknown instrument: {', '.join(unknown)}")
    return tuple(name for name in INSTRUMENT_NAMES if name in requested)


def freeze_refusal() -> str:
    """The §4.4 refusal text: freezing is a stage-B act, and these preconditions must hold."""
    lines = [
        "release --freeze is refused in Stage A (freezing is a stage-B act).",
        "Its preconditions, recorded so stage B cannot forget them:",
    ]
    lines.extend(f"  - {precondition}" for precondition in FREEZE_PRECONDITIONS)
    return "\n".join(lines)


async def _cut_command(options: argparse.Namespace, gold_root: Path) -> int:
    """Run `release cut --draft` and print the resulting release identity."""
    from tools.mem01_verify import db

    async with db.readonly_corpus_snapshot(options.org) as conn:
        info = await cut_draft_release(gold_root, options.name, options.org, conn)
    print(f"cut draft release {options.name} at {info.path}")
    print(f"release_lock_sha256 {info.lock_sha256}")
    print(f"visible files verified {info.visible_files_verified}")
    return 0


async def _instruments_command(options: argparse.Namespace, gold_root: Path) -> int:
    """Run the selected instruments into an existing draft release."""
    from tools.mem01_verify import db

    release_dir = gold_root / "releases" / options.name
    selected = _selected_instruments(options.subcommand, getattr(options, "only", None))
    manifest_builder.read_manifest(release_dir)
    criteria = load_criteria(release_dir / manifest_builder.CRITERIA_NAME)
    async with db.readonly_corpus_snapshot(options.org) as conn:
        ran = await run_instruments(release_dir, options.org, conn, selected, criteria)
    print(f"instruments written into {release_dir}: {', '.join(ran) if ran else 'none'}")
    print("re-run 'release cut --draft' to re-manifest them")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, refuse a freeze, otherwise run the command; return the exit code."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    options = build_parser().parse_args(argv)
    if options.freeze:
        print(freeze_refusal(), file=sys.stderr)
        return 2
    gold_root = options.gold_root or default_gold_root()
    try:
        if options.subcommand == "cut":
            return asyncio.run(_cut_command(options, gold_root))
        return asyncio.run(_instruments_command(options, gold_root))
    except Mem01Error as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
