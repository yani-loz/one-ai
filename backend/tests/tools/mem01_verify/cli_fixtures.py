"""
Role: The child-process runners for both §3.1 CLI forms and the session-cached release
      factories — the draft release the instrument cuts over the probe corpus (cut twice, so
      idempotence is observable before the instruments run), the §13/§16.12 baseline pair
      (cut → run → `instruments` → cut → run), a draft cut for the small org, and the synthetic
      FROZEN releases (§16.6/§16.10/§16.13) derived from the draft for the hidden-split seals.
Used by: conftest.py (re-exported as fixtures), scenario_fixtures.py.
Depends on: tests.tools.mem01_verify.session_state, .reference (subprocess plumbing),
      .frozen_release (builder); pytest.
Key invariants:
  - `POSTGRES_DB` names the probe in every child, so the "configured database" of each run is
    the synthetic corpus; the real corpus is never opened.
  - Every factory is lazy and idempotent through `SESSION_STATE` (test-env brief §5).
  - The layout right after the first cut is captured by the fixture (`initial_files`,
    `initial_manifest`) because `instruments` mutates the shared release later in the session.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from tests.tools.mem01_verify import frozen_release as frozen_builder
from tests.tools.mem01_verify import reference
from tests.tools.mem01_verify.reference import CliRun
from tests.tools.mem01_verify.session_state import (
    BACKEND_ROOT,
    CLI_TIMEOUT_SECONDS,
    RELEASE_NAME,
    REPO_ROOT,
    RUNNER_FOLDER,
    SESSION_STATE,
    CliForm,
    CliRunner,
    DevServer,
    ProbeCorpusFactory,
)

SMALL_RELEASE_NAME = "oracle-small-v1"

# ── child-process runners for the two CLI forms ───────────────────────────────────────────


def _child_env(
    server: DevServer,
    database: str,
    gold_root: Path,
    hidden_root: Path | None,
    extra_env: dict[str, str] | None,
) -> dict[str, str]:
    overrides = {
        "POSTGRES_HOST": server.host,
        "POSTGRES_PORT": str(server.port),
        "POSTGRES_DB": database,
        "MEM01_GOLD_ROOT": str(gold_root),
        "MEM01_HIDDEN_ROOT": str(hidden_root if hidden_root else gold_root / "absent-hidden-root"),
    }
    if extra_env:
        overrides.update(extra_env)
    return reference.clean_child_env(overrides)


@pytest.fixture
def run_cli(dev_server: DevServer) -> CliRunner:
    """Factory: run verify_step1 in a child process against `database` as the configured DB.

    `POSTGRES_DB` is an allowlisted setting, so the child treats the probe as its configured
    database; the real corpus is never opened. Both §3.1 forms are available via `form`.
    """

    async def run(
        args: Sequence[str],
        *,
        database: str,
        gold_root: Path,
        hidden_root: Path | None = None,
        form: CliForm = "module",
        extra_env: dict[str, str] | None = None,
        timeout_seconds: float = CLI_TIMEOUT_SECONDS,
    ) -> CliRun:
        env = _child_env(dev_server, database, gold_root, hidden_root, extra_env)
        if form == "module":
            argv = [sys.executable, "-m", "tools.mem01_verify.verify_step1", *args]
            cwd = BACKEND_ROOT
        else:
            argv = [
                sys.executable,
                str(BACKEND_ROOT / "tools" / "mem01_verify" / "verify_step1.py"),
                *args,
            ]
            cwd = REPO_ROOT
        return await asyncio.to_thread(reference.run_subprocess, argv, cwd, env, timeout_seconds)

    return run


@pytest.fixture
def run_release_cli(dev_server: DevServer) -> CliRunner:
    """Factory: run `python -m tools.mem01_verify.release ...` from backend/ in a child process."""

    async def run(
        args: Sequence[str],
        *,
        database: str,
        gold_root: Path,
        hidden_root: Path | None = None,
        extra_env: dict[str, str] | None = None,
        timeout_seconds: float = CLI_TIMEOUT_SECONDS,
    ) -> CliRun:
        env = _child_env(dev_server, database, gold_root, hidden_root, extra_env)
        argv = [sys.executable, "-m", "tools.mem01_verify.release", *args]
        return await asyncio.to_thread(
            reference.run_subprocess, argv, BACKEND_ROOT, env, timeout_seconds
        )

    return run


# ── the session draft release, the small-org draft and the baseline pair ──────────────────


@dataclass(frozen=True)
class DraftRelease:
    """A draft release cut by the instrument over the session probe corpus (cut twice)."""

    gold_root: Path
    path: Path
    name: str
    org_id: UUID
    database: str
    cut: CliRun
    initial_files: frozenset[str]
    initial_dirs: frozenset[str]
    initial_manifest: bytes
    recut: CliRun
    manifest_after_recut: bytes


DraftReleaseFactory = Callable[[], Awaitable[DraftRelease]]


def release_arguments(subcommand: str, gold_root: Path, org_id: UUID, name: str) -> list[str]:
    """The `release <subcommand> --draft` argument list (cut and instruments share it)."""
    return [
        subcommand,
        "--draft",
        "--gold-root",
        str(gold_root),
        "--name",
        name,
        "--org",
        str(org_id),
    ]


def cut_arguments(gold_root: Path, org_id: UUID, name: str = RELEASE_NAME) -> list[str]:
    """The `release cut --draft` argument list used for every cut and re-cut in the suite."""
    return release_arguments("cut", gold_root, org_id, name)


def instruments_arguments(gold_root: Path, org_id: UUID, name: str = RELEASE_NAME) -> list[str]:
    """The `release instruments --draft` argument list (§16.12; mirrors the cut's options)."""
    return release_arguments("instruments", gold_root, org_id, name)


def relative_files(release: Path) -> frozenset[str]:
    """Posix paths of every regular file under a release except `reports/**`."""
    return frozenset(
        p.relative_to(release).as_posix()
        for p in release.rglob("*")
        if p.is_file() and not p.relative_to(release).as_posix().startswith("reports/")
    )


def relative_dirs(release: Path) -> frozenset[str]:
    """Posix paths of every directory under a release."""
    return frozenset(p.relative_to(release).as_posix() for p in release.rglob("*") if p.is_dir())


@pytest.fixture
def draft_release(
    probe_corpus: ProbeCorpusFactory,
    run_release_cli: CliRunner,
    tmp_path_factory: pytest.TempPathFactory,
) -> DraftReleaseFactory:
    """Factory: cut (once per session) a draft release under a temporary gold root, twice."""

    async def ensure() -> DraftRelease:
        cached = SESSION_STATE.get("draft_release")
        if cached is not None:
            return cached  # type: ignore[return-value]
        corpus = await probe_corpus()
        gold_root = tmp_path_factory.mktemp("gold_root")
        path = gold_root / "releases" / RELEASE_NAME
        cut = await run_release_cli(
            cut_arguments(gold_root, corpus.big.org_id),
            database=corpus.database,
            gold_root=gold_root,
        )
        assert cut.exit_code == 0, f"draft cut failed (exit {cut.exit_code}): {cut.stderr[-1500:]}"
        initial_files = relative_files(path)
        initial_dirs = relative_dirs(path)
        initial_manifest = (path / "dataset.manifest.json").read_bytes()
        recut = await run_release_cli(
            cut_arguments(gold_root, corpus.big.org_id),
            database=corpus.database,
            gold_root=gold_root,
        )
        release = DraftRelease(
            gold_root=gold_root,
            path=path,
            name=RELEASE_NAME,
            org_id=corpus.big.org_id,
            database=corpus.database,
            cut=cut,
            initial_files=initial_files,
            initial_dirs=initial_dirs,
            initial_manifest=initial_manifest,
            recut=recut,
            manifest_after_recut=(path / "dataset.manifest.json").read_bytes(),
        )
        SESSION_STATE["draft_release"] = release
        return release

    return ensure


@pytest.fixture
def small_release(
    probe_corpus: ProbeCorpusFactory,
    run_release_cli: CliRunner,
    tmp_path_factory: pytest.TempPathFactory,
) -> DraftReleaseFactory:
    """Factory: a draft release cut once per session over the SMALL org (below-minimum seals)."""

    async def ensure() -> DraftRelease:
        cached = SESSION_STATE.get("small_release")
        if cached is not None:
            return cached  # type: ignore[return-value]
        corpus = await probe_corpus()
        gold_root = tmp_path_factory.mktemp("gold_root_small")
        path = gold_root / "releases" / SMALL_RELEASE_NAME
        cut = await run_release_cli(
            cut_arguments(gold_root, corpus.small.org_id, SMALL_RELEASE_NAME),
            database=corpus.database,
            gold_root=gold_root,
        )
        assert cut.exit_code == 0, f"small cut failed (exit {cut.exit_code}): {cut.stderr[-1500:]}"
        manifest = (path / "dataset.manifest.json").read_bytes()
        release = DraftRelease(
            gold_root=gold_root,
            path=path,
            name=SMALL_RELEASE_NAME,
            org_id=corpus.small.org_id,
            database=corpus.database,
            cut=cut,
            initial_files=relative_files(path),
            initial_dirs=relative_dirs(path),
            initial_manifest=manifest,
            recut=cut,
            manifest_after_recut=manifest,
        )
        SESSION_STATE["small_release"] = release
        return release

    return ensure


@dataclass(frozen=True)
class BaselinePair:
    """§13/§16.12: run 1, `instruments`, the re-cut, and run 2 over one corpus."""

    release: DraftRelease
    before: CliRun
    manifest_before: bytes
    instruments: CliRun
    files_after_instruments: frozenset[str]
    recut: CliRun
    after: CliRun


BaselinePairFactory = Callable[[], Awaitable[BaselinePair]]


@pytest.fixture
def baseline_pair(
    draft_release: DraftReleaseFactory, run_cli: CliRunner, run_release_cli: CliRunner
) -> BaselinePairFactory:
    """Factory: the two full tuning runs of §13 with the instruments cut between them (cached)."""

    async def ensure() -> BaselinePair:
        cached = SESSION_STATE.get("baseline_pair")
        if cached is not None:
            return cached  # type: ignore[return-value]
        release = await draft_release()
        before = await run_cli(
            ["--baseline-label", "before-census"],
            database=release.database,
            gold_root=release.gold_root,
        )
        manifest_before = (release.path / "dataset.manifest.json").read_bytes()
        instruments = await run_release_cli(
            instruments_arguments(release.gold_root, release.org_id),
            database=release.database,
            gold_root=release.gold_root,
        )
        files_after_instruments = relative_files(release.path)
        recut = await run_release_cli(
            cut_arguments(release.gold_root, release.org_id),
            database=release.database,
            gold_root=release.gold_root,
        )
        after = await run_cli(
            ["--baseline-label", "after-census"],
            database=release.database,
            gold_root=release.gold_root,
        )
        pair = BaselinePair(
            release=release,
            before=before,
            manifest_before=manifest_before,
            instruments=instruments,
            files_after_instruments=files_after_instruments,
            recut=recut,
            after=after,
        )
        SESSION_STATE["baseline_pair"] = pair
        return pair

    return ensure


# ── synthetic frozen releases derived from the session draft ──────────────────────────────

FrozenReleaseFactory = Callable[..., Awaitable[frozen_builder.FrozenRelease]]
FROZEN_VARIANTS = ("valid", "wrong_runner", "unopenable")


@pytest.fixture
def frozen_release(
    draft_release: DraftReleaseFactory,
    tmp_path_factory: pytest.TempPathFactory,
) -> FrozenReleaseFactory:
    """Factory: a frozen release (§16.6) for `hidden_split`, cached per (split, variant).

    `valid` carries the real runner hash and consistent hidden entries; `wrong_runner` flips
    one runner-hash digit so the §3.10 refusal can be sealed; `unopenable` keeps the real
    runner hash but flips every hidden entry's hash, so any hidden open surfaces as a lock
    error (§16.13 refusal seals).
    """

    async def ensure(hidden_split: str, *, variant: str = "valid") -> frozen_builder.FrozenRelease:
        assert variant in FROZEN_VARIANTS, variant
        key = f"frozen_release:{hidden_split}:{variant}"
        cached = SESSION_STATE.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        draft = await draft_release()
        runner_sha256 = reference.merkle_sha256_reference(RUNNER_FOLDER)
        if variant == "wrong_runner":
            runner_sha256 = frozen_builder.flip_one_hex_digit(runner_sha256)
        built = frozen_builder.build_frozen_release(
            draft.path,
            tmp_path_factory.mktemp(f"frozen_{hidden_split}_{variant}"),
            runner_sha256=runner_sha256,
            hidden_split=hidden_split,  # type: ignore[arg-type]
            tamper_hidden=variant == "unopenable",
        )
        SESSION_STATE[key] = built
        return built

    return ensure
