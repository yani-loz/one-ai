"""
Role: Loads and validates the criteria annex (`release/criteria.step1.v1.yaml`) — the formula
      sheet of the 17-gate exam (contract §4.5) — into the frozen `CriteriaFile` / `Criterion` /
      `ConfigArtifact` types of §1.4, and exposes `criteria_sha256`, the annex identity the
      release manifest and `config_hash` use.
Used by: tools.mem01_verify.run_identity (closure + config_hash), .release (cut/manifest),
      .gates.* (thresholds, minimums, edge policies), .verify_step1 (criteria entries).
Depends on: tools.mem01_verify.exceptions (CriteriaError), .hashing (`sha256_file`), .statuses
      (`GATE_NAMES`, the single §1.3 gate roster); pyyaml (safe_load only).
Key invariants:
  - The annex is DATA: this module never edits it and never hands back a mutable view — every
    field of the returned object is a tuple or a read-only mapping on a frozen dataclass.
  - Validation is total and fails closed — a missing/unknown field, an unknown or missing gate
    block, a duplicate id, a `zero_denominator` other than `error`, a `ratio` with `minimum` < 1,
    a `count` carrying a denominator or a minimum, a dangling partner id, a gate with no
    non-diagnostic criterion, a holdout gate with no validation entry, or a `provisional_gates`
    list other than the frozen four in order all raise `CriteriaError`, never a bare `ValueError`
    and never a partially built `CriteriaFile`.
  - `PROVISIONAL_GATES` order is frozen: the verdict line prints it verbatim.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import yaml

from tools.mem01_verify.exceptions import CriteriaError
from tools.mem01_verify.hashing import sha256_file
from tools.mem01_verify.statuses import GATE_NAMES

# The 17 gates of §1.3 in the frozen order. `statuses` is the one place the roster is written
# down; the wave-3 `gates.registry` cross-checks its own tuple against it at import time.
CRITERIA_GATES: tuple[str, ...] = GATE_NAMES
PROVISIONAL_GATES: tuple[str, ...] = ("FID", "THR", "IDENT", "ATTR")

_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "criteria_version", "release_state", "founder_defaults", "provisional_gates",
    "directional_gates", "env_allowlist", "config_files", "scope_policy", "qs_surfaces",
    "red_surfaces", "chunk_policy", "leakage_policy", "gates",
)  # fmt: skip
_CRITERION_FIELDS: tuple[str, ...] = (
    "id", "set", "evidence_basis", "split_source", "kind", "formula", "numerator_def",
    "denominator_def", "operator", "threshold", "minimum", "zero_denominator", "edge_policies",
    "worked_example", "partner", "stage_available",
)  # fmt: skip
# `type` (Z|R) is annex prose §1.4 omits; `*_founder_draft` flags a value the founder confirms at
# the stage-B freeze. Both are tolerated on a criterion; any other key is a fault.
_OPTIONAL_CRITERION_FIELDS: frozenset[str] = frozenset({"type", "diagnostic_only", "directional"})
_FOUNDER_DRAFT_SUFFIX = "_founder_draft"

_RELEASE_STATES = frozenset({"draft", "frozen"})
_SPLIT_SOURCES = frozenset({"optimization", "test", "validation", "fixtures", "corpus"})
_KINDS = frozenset({"ratio", "count"})
_OPERATORS = frozenset({"==", "<=", ">="})
_STAGES = frozenset({"A", "B", "C", "D", "E", "F"})
_ZERO_DENOMINATORS = frozenset({"error"})
_FILE = "criteria file"  # the `where` label of every top-level field message
_Yaml = Mapping[str, object]  # one YAML mapping as pyyaml returns it


@dataclass(frozen=True, slots=True)
class ConfigArtifact:
    """A declared configuration input of the run closure; `hashed=False` only for secret files."""

    path: str
    hashed: bool
    role: str


@dataclass(frozen=True, slots=True)
class Criterion:
    """One criterion of the exam — a conjunction member, never a weighted score (§1.4/§4.5)."""

    id: str
    gate: str
    set: str
    evidence_basis: str
    split_source: Literal["optimization", "test", "validation", "fixtures", "corpus"]
    kind: Literal["ratio", "count"]
    formula: str
    numerator_def: str
    denominator_def: str | None
    operator: Literal["==", "<=", ">="]
    threshold: float
    minimum: int | None
    zero_denominator: Literal["error"]
    edge_policies: tuple[str, ...]
    worked_example: str
    partner: tuple[str, ...]
    stage_available: Literal["A", "B", "C", "D", "E", "F"]
    diagnostic_only: bool = False
    directional: bool = False


@dataclass(frozen=True, slots=True)
class CriteriaFile:
    """The whole annex: the frozen header decisions plus every criterion, indexed by gate."""

    criteria_version: str
    release_state: Literal["draft", "frozen"]
    # Policy blocks hold prose of mixed shape (str/bool/int/list): `object` is the honest value.
    founder_defaults: tuple[Mapping[str, object], ...]
    provisional_gates: tuple[str, ...]
    directional_gates: tuple[str, ...]
    env_allowlist: tuple[str, ...]
    config_files: tuple[ConfigArtifact, ...]
    scope_policy: Mapping[str, object]
    qs_surfaces: tuple[str, ...]
    red_surfaces: tuple[str, ...]
    chunk_policy: Mapping[str, object]
    leakage_policy: Mapping[str, object]
    criteria: tuple[Criterion, ...]
    by_gate: Mapping[str, tuple[Criterion, ...]]


def criteria_sha256(path: Path) -> str:
    """Return the sha256 of the annex file's bytes; CriteriaError when it cannot be read."""
    try:
        return sha256_file(path)
    except OSError as error:
        raise CriteriaError(f"criteria annex unreadable at {path}: {error}") from error


def load_criteria(path: Path) -> CriteriaFile:
    """Parse and fully validate the annex YAML at `path` (or a copy of it).

    Returns the frozen `CriteriaFile`, `criteria` in gate order and `by_gate` covering all 17
    gates. Raises CriteriaError on an unreadable file, invalid YAML, a non-mapping document, or
    any §4.5 violation.
    """
    document = _read_document(path)
    _require_keys(document, _TOP_LEVEL_FIELDS, _FILE)
    criteria, by_gate = _parse_gates(document["gates"])
    provisional = _string_list(document["provisional_gates"], "provisional_gates", _FILE)
    if provisional != PROVISIONAL_GATES:
        raise CriteriaError(f"'provisional_gates' must be {list(PROVISIONAL_GATES)} in that order")
    return CriteriaFile(
        criteria_version=_text(document, "criteria_version", _FILE),
        release_state=_choice(document, "release_state", _RELEASE_STATES, _FILE),
        founder_defaults=_mapping_list(document, "founder_defaults"),
        provisional_gates=provisional,
        directional_gates=_directional_gates(document),
        env_allowlist=_string_list(document["env_allowlist"], "env_allowlist", _FILE),
        config_files=_config_artifacts(document),
        scope_policy=_policy_block(document, "scope_policy"),
        qs_surfaces=_string_list(document["qs_surfaces"], "qs_surfaces", _FILE),
        red_surfaces=_surface_names(document),
        chunk_policy=_policy_block(document, "chunk_policy"),
        leakage_policy=_policy_block(document, "leakage_policy"),
        criteria=criteria,
        by_gate=MappingProxyType(dict(by_gate)),
    )


def _read_document(path: Path) -> _Yaml:
    """Read and YAML-parse the annex, refusing anything that is not a mapping."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CriteriaError(f"criteria annex unreadable at {path}: {error}") from error
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise CriteriaError(f"criteria annex at {path} is not valid YAML: {error}") from error
    if not isinstance(document, Mapping):
        raise CriteriaError(f"criteria annex at {path} is not a mapping")
    return document


def _parse_gates(blocks: object) -> tuple[tuple[Criterion, ...], dict[str, tuple[Criterion, ...]]]:
    """Parse every gate block in the frozen gate order, then apply the cross-criterion rules."""
    if not isinstance(blocks, Mapping):
        raise CriteriaError("criteria file field 'gates' must be a mapping of gate name to block")
    present = frozenset(str(name) for name in blocks)
    for label, wrong in (("unknown", present - frozenset(CRITERIA_GATES)),
                         ("missing", frozenset(CRITERIA_GATES) - present)):  # fmt: skip
        if wrong:
            raise CriteriaError(f"{label} gate block(s) in the {_FILE}: {', '.join(sorted(wrong))}")
    by_gate = {gate: _parse_gate_block(gate, blocks[gate]) for gate in CRITERIA_GATES}
    ordered = tuple(criterion for gate in CRITERIA_GATES for criterion in by_gate[gate])
    _check_ids_and_partners(ordered)
    _check_holdout_entries(by_gate)
    return ordered, by_gate


def _parse_gate_block(gate: str, block: object) -> tuple[Criterion, ...]:
    """Parse one gate's block; every gate needs a title and >= 1 non-diagnostic criterion."""
    if not isinstance(block, Mapping):
        raise CriteriaError(f"gate {gate}: block must be a mapping")
    if not _is_text(block.get("title")):
        raise CriteriaError(f"gate {gate}: field 'title' must be a non-empty string")
    entries = block.get("criteria")
    if not _is_list(entries) or not entries:
        raise CriteriaError(f"gate {gate}: field 'criteria' must be a non-empty list")
    criteria = tuple(_parse_criterion(gate, entry) for entry in entries)  # type: ignore[union-attr]
    if all(criterion.diagnostic_only for criterion in criteria):
        raise CriteriaError(f"gate {gate}: every gate needs >= 1 non-diagnostic criterion")
    return criteria


def _parse_criterion(gate: str, entry: object) -> Criterion:
    """Parse and validate one criterion entry of a gate block."""
    if not isinstance(entry, Mapping):
        raise CriteriaError(f"gate {gate}: every criterion must be a mapping")
    where = f"gate {gate} criterion {entry.get('id', '<no id>')!r}"
    _require_keys(entry, _CRITERION_FIELDS, where, closed=True)
    criterion_id = _text(entry, "id", where)
    if not criterion_id.startswith(f"{gate.lower()}."):
        raise CriteriaError(f"{where}: id must start with '{gate.lower()}.'")
    if _text(entry, "set", where) != gate:
        raise CriteriaError(f"{where}: field 'set' must equal the gate name {gate}")
    kind = _choice(entry, "kind", _KINDS, where)
    denominator, minimum = _kind_bounds(entry, kind, where)
    threshold = entry["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, int | float):
        raise CriteriaError(f"{where}: field 'threshold' must be a number")
    return Criterion(
        id=criterion_id,
        gate=gate,
        set=gate,
        evidence_basis=_text(entry, "evidence_basis", where),
        split_source=_choice(entry, "split_source", _SPLIT_SOURCES, where),
        kind=kind,
        formula=_text(entry, "formula", where),
        numerator_def=_text(entry, "numerator_def", where),
        denominator_def=denominator,
        operator=_choice(entry, "operator", _OPERATORS, where),
        threshold=float(threshold),
        minimum=minimum,
        zero_denominator=_choice(entry, "zero_denominator", _ZERO_DENOMINATORS, where),
        edge_policies=_string_list(entry.get("edge_policies"), "edge_policies", where),
        worked_example=_text(entry, "worked_example", where),
        partner=_string_list(entry.get("partner"), "partner", where, allow_empty=True),
        stage_available=_choice(entry, "stage_available", _STAGES, where),
        diagnostic_only=_flag(entry, "diagnostic_only", where),
        directional=_flag(entry, "directional", where),
    )


def _kind_bounds(entry: _Yaml, kind: str, where: str) -> tuple[str | None, int | None]:
    """Return `(denominator_def, minimum)` after applying the `kind` rules of §4.5."""
    denominator = entry["denominator_def"]
    minimum = entry["minimum"]
    if kind == "ratio":
        if not _is_text(denominator):
            raise CriteriaError(f"{where}: a ratio needs a non-empty 'denominator_def'")
        integral = isinstance(minimum, int) and not isinstance(minimum, bool)
        if not integral or int(minimum) < 1:  # type: ignore[arg-type]
            raise CriteriaError(f"{where}: a ratio needs an integer 'minimum' >= 1")
        return str(denominator), int(minimum)  # type: ignore[arg-type]
    if denominator is not None:
        raise CriteriaError(f"{where}: a count criterion must have a null 'denominator_def'")
    if minimum is not None:
        raise CriteriaError(f"{where}: a count criterion must have a null 'minimum'")
    return None, None


def _check_ids_and_partners(criteria: Sequence[Criterion]) -> None:
    """Ids are unique across the annex and every partner id names a criterion that exists."""
    counts = Counter(criterion.id for criterion in criteria)
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    if duplicates:
        raise CriteriaError(f"duplicate criterion id(s): {', '.join(duplicates)}")
    dangling = sorted(f"{c.id} -> {p}" for c in criteria for p in c.partner if p not in counts)
    if dangling:
        raise CriteriaError(f"partner id(s) that do not exist: {', '.join(dangling)}")


def _check_holdout_entries(by_gate: Mapping[str, tuple[Criterion, ...]]) -> None:
    """Each holdout gate carries provisional entries AND validation-split entries (§4.5)."""
    for gate in PROVISIONAL_GATES:
        slug, entries = gate.lower(), by_gate[gate]
        if not any(f"{slug}.provisional" in criterion.id for criterion in entries):
            raise CriteriaError(f"gate {gate}: no '{slug}.provisional…' criterion")
        wanted = f"{slug}.validation"
        if not any(c.id.startswith(wanted) and c.split_source == "validation" for c in entries):
            raise CriteriaError(f"gate {gate}: no '{wanted}…' criterion on the validation split")


def _config_artifacts(document: _Yaml) -> tuple[ConfigArtifact, ...]:
    """Parse `config_files` into `ConfigArtifact`s (path, hashed flag, role prose)."""
    where = f"{_FILE} field 'config_files'"
    artifacts: list[ConfigArtifact] = []
    for entry in _mapping_list(document, "config_files"):
        path = _text(entry, "path", where)
        hashed = entry.get("hashed")
        if not isinstance(hashed, bool):
            raise CriteriaError(f"{where}: entry {path!r} needs a boolean 'hashed'")
        artifacts.append(ConfigArtifact(path=path, hashed=hashed, role=_text(entry, "role", where)))
    return tuple(artifacts)


def _surface_names(document: _Yaml) -> tuple[str, ...]:
    """`red_surfaces` entries are `{name, stage_available}` objects (bare names also accepted)."""
    entries = document["red_surfaces"]
    if not _is_list(entries) or not entries:
        raise CriteriaError(f"{_FILE} field 'red_surfaces' must be a non-empty list")
    names = [e.get("name") if isinstance(e, Mapping) else e for e in entries]  # type: ignore[union-attr]
    if not all(_is_text(name) for name in names):
        raise CriteriaError(f"{_FILE} field 'red_surfaces': every entry needs a 'name'")
    return tuple(str(name) for name in names)


def _directional_gates(document: _Yaml) -> tuple[str, ...]:
    """`directional_gates` is a possibly empty list of names drawn from the 17 gates."""
    key = "directional_gates"
    names = _string_list(document[key], key, _FILE, allow_empty=True)
    unknown = sorted(name for name in names if name not in CRITERIA_GATES)
    if unknown:
        raise CriteriaError(f"{key!r} names unknown gate(s): {', '.join(unknown)}")
    return names


def _mapping_list(document: _Yaml, key: str) -> tuple[_Yaml, ...]:
    """A non-empty list of mappings under a top-level key, returned read-only."""
    entries = document[key]
    if not _is_list(entries) or not entries:
        raise CriteriaError(f"{_FILE} field {key!r} must be a non-empty list")
    if not all(isinstance(entry, Mapping) for entry in entries):  # type: ignore[union-attr]
        raise CriteriaError(f"{_FILE} field {key!r} must contain only mappings")
    return tuple(MappingProxyType(dict(entry)) for entry in entries)  # type: ignore[union-attr,arg-type]


def _policy_block(document: _Yaml, key: str) -> _Yaml:
    """A policy block, returned as a read-only mapping."""
    value = document[key]
    if not isinstance(value, Mapping):
        raise CriteriaError(f"{_FILE} field {key!r} must be a mapping")
    return MappingProxyType(dict(value))


def _string_list(
    value: object, key: str, where: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    """Coerce a YAML list of non-empty strings to a tuple, refusing anything else."""
    if not _is_list(value):
        raise CriteriaError(f"{where}: field {key!r} must be a list of strings")
    if not value and not allow_empty:  # type: ignore[arg-type]
        raise CriteriaError(f"{where}: field {key!r} must not be empty")
    if not all(_is_text(item) for item in value):  # type: ignore[union-attr]
        raise CriteriaError(f"{where}: field {key!r} must contain only non-empty strings")
    return tuple(str(item) for item in value)  # type: ignore[union-attr]


def _require_keys(entry: _Yaml, keys: Iterable[str], where: str, *, closed: bool = False) -> None:
    """Every listed key must be present (a null value is fine); `closed` also bans extra keys."""
    known = frozenset(keys)
    missing = sorted(key for key in known if key not in entry)
    if missing:
        raise CriteriaError(f"{where}: missing field(s) {', '.join(missing)}")
    if not closed:
        return
    allowed = known | _OPTIONAL_CRITERION_FIELDS
    unknown = sorted(
        key for key in entry if key not in allowed and not str(key).endswith(_FOUNDER_DRAFT_SUFFIX)
    )
    if unknown:
        raise CriteriaError(f"{where}: unknown field(s) {', '.join(unknown)}")


def _text(entry: _Yaml, key: str, where: str) -> str:
    """A required non-empty string field."""
    value = entry.get(key)
    if not _is_text(value):
        raise CriteriaError(f"{where}: field {key!r} must be a non-empty string")
    return str(value)


def _choice(entry: _Yaml, key: str, allowed: frozenset[str], where: str) -> str:
    """A required string field restricted to `allowed` (assigned into the caller's Literal)."""
    value = _text(entry, key, where)
    if value not in allowed:
        raise CriteriaError(f"{where}: field {key!r} must be one of {sorted(allowed)}")
    return value


def _flag(entry: _Yaml, key: str, where: str) -> bool:
    """An optional boolean marker, defaulting to False."""
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise CriteriaError(f"{where}: field {key!r} must be a boolean when present")
    return value


def _is_text(value: object) -> bool:
    """True for a non-empty, non-blank string."""
    return isinstance(value, str) and bool(value.strip())


def _is_list(value: object) -> bool:
    """True for a YAML sequence (a string is not a sequence here)."""
    return isinstance(value, Sequence) and not isinstance(value, str)
