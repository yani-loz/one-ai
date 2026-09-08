"""
Role: The input observer of contract §3.11 — the telemetry half of the run closure. It records
      every file this interpreter opens and every environment variable it reads while a run is
      in flight, and answers which of those reads fall OUTSIDE the declared closure.
Used by: tools.mem01_verify.run_identity (which re-exports `InputObserver` as the §1.3 public
      name), and through it tools.mem01_verify.verify_step1 (step 9) and .runner_steps
      (`check_observer`); sealed by tests/tools/mem01_verify/test_observer_scope.py,
      test_run_identity.py and test_review_round_a.py.
Depends on: tools.mem01_verify.hashing (`is_bytecode`, the ONE bytecode predicate) and, for
      typing only, tools.mem01_verify.run_identity (`Closure` — imported under TYPE_CHECKING so
      the pair never forms an import cycle); stdlib os/sys/pathlib.
Key invariants:
  - Observation is telemetry over THIS interpreter's own `open` audit events and environment
    reads (§3.11), never native or child-process reads; paths are absolute and only reads inside
    the repository root are ever reported as offenders.
  - The audit hook is process-permanent once installed (CPython forbids removing one); the
    environment reader is restored as soon as the last active observer exits, so a process that
    ran a closed observer pays nothing.
  - §16.16(c)/(r): reads under a pinned dependency tree (`backend/.venv/`) and reads of BYTECODE
    — decided ONLY by `hashing.is_bytecode`, with no directory taking part — stay in
    `observed_paths` but are never offenders. Every OTHER file under a `__pycache__` directory
    follows the ordinary closure test: an offender outside the closure, never one inside the
    editable scope (where it is hashed too).
  - Nothing here mutates the closure and nothing raises during observation: a hook that cannot
    decode its argument records nothing rather than failing the run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

from tools.mem01_verify.hashing import is_bytecode

if TYPE_CHECKING:  # pragma: no cover - typing only; the runtime pair must not form a cycle
    from tools.mem01_verify.run_identity import Closure

PINNED_DEPENDENCY_TREES: tuple[str, ...] = ("backend/.venv",)
"""Trees exempt from the observer's closure test (§16.16(c)/(r)): observed, never offenders.
Justification (D1): the DECLARED dependency set — `backend/pyproject.toml`, `backend/uv.lock`,
`backend/.python-version` — is part of `code_hash`, so a dependency change moves the run
identity; the venv's own bytes are NOT pinned and are never hashed."""

RELEASE_OPTION = "release"
"""The CLI option whose value names a release directory reads may legitimately fall under."""

_ACTIVE_OBSERVERS: list[InputObserver] = []
_ENVIRON_TYPE = type(os.environ)
_ORIGINAL_GETITEM = _ENVIRON_TYPE.__getitem__
_AUDIT_HOOK_INSTALLED = False


def _audit_open_hook(event: str, args: tuple[object, ...]) -> None:
    """Record every `open` event while at least one observer is active; never raises."""
    if event != "open" or not _ACTIVE_OBSERVERS or not args:
        return
    target = args[0]
    if target is None or isinstance(target, int):
        return
    try:
        absolute = Path(os.path.abspath(os.fsdecode(target)))
    except (TypeError, ValueError):
        return
    for observer in _ACTIVE_OBSERVERS:
        observer.record_path(absolute)


def _recording_getitem(environ: object, key: str) -> str:
    """`os._Environ.__getitem__` with observation — also covers `.get()` and `os.getenv()`."""
    if _ACTIVE_OBSERVERS and isinstance(key, str):
        for observer in _ACTIVE_OBSERVERS:
            observer.record_env(key)
    return _ORIGINAL_GETITEM(environ, key)


def _install_probes() -> None:
    """Install the audit hook (once per process) and the environment reader."""
    global _AUDIT_HOOK_INSTALLED
    if not _AUDIT_HOOK_INSTALLED:
        sys.addaudithook(_audit_open_hook)
        _AUDIT_HOOK_INSTALLED = True
    _ENVIRON_TYPE.__getitem__ = _recording_getitem  # type: ignore[method-assign]


def _remove_probes() -> None:
    """Restore the original environment reader (the audit hook is process-permanent)."""
    _ENVIRON_TYPE.__getitem__ = _ORIGINAL_GETITEM  # type: ignore[method-assign]


def _is_pinned_dependency(relative: str) -> bool:
    """True for a repo-relative read the closure accounts for outside the scope walk.

    Exactly two exemptions (§16.16(c)/(r)): a pinned dependency tree (`backend/.venv/`), and
    BYTECODE anywhere — recognised by `hashing.is_bytecode`, the same suffix-chain predicate the
    scope walk and the release manifest use, with NO directory taking part (A26). Any OTHER file
    under `__pycache__` is an ordinary path and follows the normal closure test.
    """
    if is_bytecode(Path(relative)):
        return True
    return any(
        relative == tree or relative.startswith(f"{tree}/") for tree in PINNED_DEPENDENCY_TREES
    )


class InputObserver:
    """Records every file this process opens and every environment variable it reads (§3.11).

    Used as a context manager around steps 2..9 of the run sequence. File observation rides the
    interpreter's `open` audit event; environment observation rides `os._Environ.__getitem__`,
    which `os.environ[...]`, `os.environ.get(...)` and `os.getenv(...)` all funnel through.
    Neither claims to see native or child-process reads — those are declared in the closure.
    """

    def __init__(self, repo_root: Path) -> None:
        """Observe reads relative to `repo_root` (the parent of `backend/`)."""
        self._repo_root = Path(os.path.abspath(repo_root))
        self._paths: set[Path] = set()
        self._env: set[str] = set()

    def __enter__(self) -> InputObserver:
        """Start observing."""
        _ACTIVE_OBSERVERS.append(self)
        _install_probes()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop observing; the recorded sets stay readable afterwards."""
        if self in _ACTIVE_OBSERVERS:  # a caller that unwound out of order left it removed
            _ACTIVE_OBSERVERS.remove(self)
        if not _ACTIVE_OBSERVERS:
            _remove_probes()

    def record_path(self, path: Path) -> None:
        """Record one opened absolute path (called by the audit hook)."""
        self._paths.add(path)

    def record_env(self, name: str) -> None:
        """Record one environment variable name that was read."""
        self._env.add(name)

    @property
    def observed_paths(self) -> frozenset[Path]:
        """Every absolute path opened while this observer was active."""
        return frozenset(self._paths)

    @property
    def observed_env(self) -> frozenset[str]:
        """Every environment variable name read while this observer was active."""
        return frozenset(self._env)

    def _repo_relative(self, path: Path) -> str | None:
        """The repo-relative posix path, or None when `path` lies outside the repository."""
        for root in (self._repo_root, self._repo_root.resolve()):
            try:
                return path.relative_to(root).as_posix()
            except ValueError:
                continue
        return None

    def _allowed_prefixes(self, closure: Closure) -> tuple[str, ...]:
        """Directory prefixes a read may fall under: editable scope, runner folder, release."""
        prefixes = [*closure.editable_scope, closure.runner_folder]
        release = closure.cli_options.get(RELEASE_OPTION)
        if isinstance(release, str | Path):
            relative = self._repo_relative(Path(os.path.abspath(release)))
            if relative:
                prefixes.append(relative)
        return tuple(prefix.rstrip("/") for prefix in prefixes)

    def check_within(self, closure: Closure) -> list[str]:
        """Return the observed reads that fall outside the closure (§3.11 `opened_outside_closure`).

        A repository file is an offender unless it lies inside the editable scope, the runner
        folder or the release directory, is a declared config artifact, or is pinned dependency
        content (§16.16(c)); a file outside the repository is never reported. An environment
        variable is an offender unless `closure.env_allowlist` names it. Offenders are absolute
        paths, or `env:<NAME>`.
        """
        prefixes = self._allowed_prefixes(closure)
        declared = set(closure.config_artifacts)
        offenders: list[str] = []
        for path in sorted(self._paths):
            relative = self._repo_relative(path)
            if relative is None or relative in declared or _is_pinned_dependency(relative):
                continue
            if any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in prefixes):
                continue
            offenders.append(str(path))
        allowed_env = set(closure.env_allowlist)
        offenders.extend(f"env:{name}" for name in sorted(self._env) if name not in allowed_env)
        return offenders
