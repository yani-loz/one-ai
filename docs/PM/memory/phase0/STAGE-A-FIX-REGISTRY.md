# STAGE A — fix registry (post-fleet review, 2026-09-07)

Source: the three adversarial lenses (security/tenancy/privacy, house rules, functional-vs-spec)
over `backend/tools/mem01_verify/` after the implementation fleet, 30 findings each adjudicated by
an independent Opus judge (9 CONFIRMED, 19 QUALIFIED, 2 REFUTED), plus the builders' own
deviation reports (W2-B, W2-E, W3-A..D). Every entry below was re-verified by the orchestrator
against the source before it was admitted (file:line quoted in the finding checked by hand).
Contract: `STAGE-A-CONTRACT.md` v1.2.7; the seal: `STAGE-A-SEAL.sha256` (amendments 1–2 logged in
`STAGE-A-SEAL-AMENDMENTS.md`).

Discipline (Bible): a fix that changes behaviour gets a SEALING TEST written by the oracle author
BEFORE the fix (seal amendment 3 = new test files only, plus the one invocation amendment in A3);
house-rule fixes (group B) change no public behaviour and are guarded by the existing seal.
Implementers never touch `backend/tests/tools/`.

## Group A — behavioural (sealing test first, then fix)

| id | findings | WHAT the instrument must do (the sealable statement) | sealing test (oracle author) | fix owner |
|---|---|---|---|---|
| A1 | #1/#22 CONFIRMED major (observer suspended across steps 4–5 and 8), #7 QUALIFIED (Alembic child inherits the whole environment), W3-D deviation 1 | (a) The Alembic child process receives an environment built ONLY from the annex `env_allowlist` names present in the parent (values passed through) plus the explicit overrides `POSTGRES_DB=<probe>`, `POSTGRES_HOST`, `POSTGRES_PORT`; nothing else — exposed as the pure function `probe_db.child_environment(probe_name: str, parent: Mapping[str, str], allowlist: Sequence[str], *, host: str, port: str) -> dict[str, str]`. (b) `InputObserver.check_within` never reports a path under `backend/.venv/` or a `__pycache__` directory as an offender (they are pinned by `uv.lock` in `code_hash`); they may still appear in `observed_paths`. (c) The annex `env_allowlist` is widened by determination §16.16 with the libpq/asyncpg names read on every connect and the platform names a child process needs (list in §16.16), PLUS (found by FA-2 after (a)–(d) landed) a THIRD block of every environment name a pinned distribution reads at import time during the run — 15 names measured on the baseline (numpy/OpenBLAS, openpyxl, Pillow, pydantic, tldextract, Windows `PATHEXT`/`PROCESSOR_*`, `XDG_CACHE_HOME`), listed in §16.16(b); they are declared, not exempted, and the sealed baseline `opened_outside_closure == []` is the guard that the list is complete for the pinned set. (d) With (a)–(c) in place the observer window is the literal §3.2 step 2→9 window: `runner_steps.declared_boundary` is deleted and the sealed baseline assertion `opened_outside_closure == []` proves it. | new `test_probe_child_env.py`: `child_environment` output keys ⊆ allowlist ∪ {POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT}, `POSTGRES_DB == probe`, a parent key outside the allowlist (e.g. `PGSSLMODE_EVIL`, `SECRET_X`) never appears, values pass through unchanged. new `test_observer_scope.py`: an observed path `backend/.venv/Lib/site-packages/x/METADATA` and one under `__pycache__` are not offenders; a path `backend/scripts/foo.py` outside the closure still is. Existing seal covers (d). | fixer A-1 (probe_db.py, run_identity.py, runner_steps.py, verify_step1.py, annex) |
| A2 | #4/#20 CONFIRMED major (aborted hidden run printed unprojected) + W3-D dispute | `result_block.project_for_stdout(block)` handles the aborted shape: for an aborted block it keeps every aborted-shape top-level key of §16.14 (incl. `reason`, `aborted_at_step`, `partial`, identity fields, `cleanup`) and, on a hidden run kind, projects `gates` (status only for hidden-evidence gates), collapses `criteria` on the evaluated split, and drops `diagnostics`, `exclusions`, `opened_outside_closure` exactly as for a completed hidden run; on tuning runs an aborted block is returned unchanged. The runner prints `project_for_stdout(block)` for EVERY run (no special case) after `validate_result_block(printed, projection="stdout")` accepts it; a rejection is an internal ERROR (exit 2) whose reason names only the violation class. | new `test_result_block_aborted_projection.py`: an aborted checkpoint block carrying a hidden-evidence gate with a `reason` and criteria entries with numerators on the `test` split → projected block keeps `reason`/`aborted_at_step`/`cleanup`, gate reduced to `{status}`, criteria collapsed per SET, no `diagnostics`/`exclusions`/`opened_outside_closure`; `validate_result_block(projected, projection="stdout")` passes; an aborted tuning block round-trips unchanged. PLUS (Codex): `validate_result_block(<aborted hidden block still carrying diagnostics / exclusions / opened_outside_closure / a hidden-evidence gate reason>, projection="stdout")` is REJECTED while the same block validates as `projection="protected"` — `check_aborted` is permissive today, so the runner's raw-print branch was unsealed; the existing sealed CLI refusal tests (which validate the printed block as a stdout projection) then force the wiring. | fixer A-2 (result_block.py, result_block_schema.py, verify_step1.py) |
| A3 | #2 QUALIFIED major / #23 (`--expect-lock` not required on a frozen release; contract §3.1 line 161: absent → allowed only on a draft) | A hidden or tuning run on a FROZEN release without `--expect-lock` is refused at step 3 (aborted, exit 2, reason `expect-lock required on a frozen release`) before any roster, reservation, admission or database access; with `--expect-lock` it proceeds as today. (The first draft of this row claimed a "seal defect" in the sealed frozen invocations — REFUTED by the oracle author and verified: `scenario_fixtures.py:42` already passes `--expect-lock` on every frozen scenario; the only lock-less `--checkpoint`/`--validation` invocations are on DRAFT releases, which §3.1 allows. No sealed file changed.) | new file `test_verify_step1_frozen_expect_lock.py`: frozen release without `--expect-lock` for tuning, `--checkpoint`, `--validation` → aborted at step 3 with the reason above, ledger and audit byte-identical, no hidden file opened (tamper-hash variant), plus one `--checkpoint` with an unreachable server (`POSTGRES_PORT=1`) that must yield the same refusal — "before any database access"; the sealed `frozen_refusals.checkpoint` run (WITH the lock → step 6) is the positive control. | fixer A-3 (runner_steps.py) |
| A4 | #27 CONFIRMED major (hidden bracket shows 0 for unscored splits; §3.6 line 268 and §16.13 require every split's cumulative counter) | On a checkpoint/validation run `hidden_budget_by_split` and the verdict bracket carry the cumulative ledger counters of ALL FOUR H splits (each keyed by its own split digest from the manifest), including splits not reserved by this run; `total = max` over the four; `limit` = effective limit of the split attaining the max. Reservation itself still covers only the scorable splits. | `test_hidden_budget_display.py` as first written is VACUOUS (it feeds all four digests to `HiddenBudget.counters`, which already handles them — confirmed by two independent runs; the defect is in `runner_steps.py:269`, which builds digests for the scorable sets only). Sealable surface: `runner_steps.hidden_display_digests(manifest) -> dict[str, str]` = `split_digest(manifest, name)` for ALL FOUR `H_SPLIT_GATES` (fails today: missing); AND (found by FA-2, ruled in §16.16(f)) `hidden_budget.split_digest(manifest, name)` returns the digest of the EMPTY per-split record set for an H split the manifest names no hidden test files for, instead of raising — such a split can never be reserved, so its counter displays 0 and its ledger key is never written; `HiddenBudgetLedgerError` stays for a name outside `H_SPLIT_GATES` or a malformed manifest (sealed by `test_display_digests_cover_all_four_h_splits_even_when_absent_from_the_manifest`); the counters test stays as the positive control; the wiring (full mapping to `counters`, scorable subset to `reserve`) is reviewer-checked until the CLI path becomes sealable in Stage C. | fixer A-4 (hidden_budget.py, runner_steps.py) |
| A5 | #24 QUALIFIED minor (step 7 opens hidden rosters of SETs never reserved) | `roster.verify_roster(release, *, split, hidden_root, sets=None)`: for a hidden split with `sets` given, only those SETs' hidden files are opened/compared; the runner passes exactly the reserved sets at step 7. | new test in `test_roster_hidden_subset.py`: hidden root with QS present and NF ABSENT; `verify_roster(..., split="test", sets=["QS"])` succeeds; without `sets` it raises (NF missing). | fixer A-5 (roster.py, runner_steps.py) |
| A6 | #26 QUALIFIED minor (§3.7 `<run_id>.sealed` not implemented) | `validation_guard.seal_aborted_attempt(report_dir: Path) -> Path` renames the aborted validation attempt's report directory to `<run_id>.sealed` (refusing with `ValidationGuardError` if the target exists) and the runner calls it after writing the aborted artifacts of an ADMITTED `--validation` attempt (admission event appended); a re-run never reads a `*.sealed` directory. | new `test_validation_guard_seal.py`: helper renames, refuses an existing target, leaves other directories untouched. | fixer A-6 (validation_guard.py, verify_step1.py) |
| A7 | #25 QUALIFIED minor (`protected_result_path` recorded as the fixed template `<run_id>/protected_result.json` while a hidden run's report dir is `<hidden_root>/releases/<name>/reports/<run_id>` — verified in `runner_steps.resolve_report_dir`) | The `protected_result_path` recorded in ledger/journal events is computed from the ACTUAL report directory: posix, relative to the hidden root on hidden runs (`releases/<name>/reports/<run_id>/protected_result.json`), relative to the release directory on tuning runs (`reports/<run_id>/protected_result.json`; relative to the `--report-dir` directory when that option is given); exposed as `runner_output.protected_result_relpath(report_dir: Path, report_root: Path) -> str`, which refuses (`IntegrityViolationError`) a report dir outside the root. | new test: helper output for a hidden-root report dir, for a release report dir, and the refusal. | fixer A-7 (runner_output.py, verify_step1.py) |
| A8 | #6 QUALIFIED minor (`keep=True` unconditional; a non-`Mem01Error` leaks the probe) | The probe is created with `keep=<the --keep-probe option>`; any exception (not only `Mem01Error`) raised between step 6 and step 11 still drops the probe unless `--keep-probe`, and the block is the `cleanup_failed` aborted block only when the drop itself fails. | no seal feasible without fault injection — fix + reviewer check. | fixer A-8 (runner_steps.py, verify_step1.py) |
| A9 | #3 QUALIFIED minor (app WARNING records reach stderr during gate evaluation; R5 forbids personal data on stderr) + W3-C note | From step 4 to step 11 every record of the `app` logger hierarchy is written to `<report_dir>/app.log` and NOT to stdout/stderr; exposed as the context manager `runner_output.capture_app_logging(report_dir: Path)`. | new `test_app_logging_capture.py`: inside the context a `logging.getLogger("app.test").warning(...)` reaches `app.log` and no stream handler; outside it, handlers are restored. | fixer A-9 (runner_output.py, verify_step1.py) |
| A10 | #7 (second half: Alembic child stderr tail on stdout) | The child's stdout/stderr never reach the runner's stdout/stderr; on failure the abort reason is `probe migration failed` and the child output is written to `<report_dir>/probe_migration.log` (or the maintenance log path when no report dir exists yet). | covered by A1's `child_environment` seal + reviewer check on the failure path. | fixer A-1 |
| A12 | fixer B-3 report: `test_verify_step1_cli.py::test_both_invocation_forms_produce_the_same_block` failed once under fleet load on `diagnostics` — `probe_db.list_stale_probe_databases` (`probe_db.py:271-280`) returns EVERY `mem01_probe_*` database on the server, live foreign probes included, so two consecutive invocations differ whenever a concurrent run's probe appears; the contract (§12 line 500, §16.4) calls a probe stale only when it was LEFT BEHIND. (Codex review 2026-09-07 caught a contradiction in the first draft of this row; corrected below.) | `list_stale_probe_databases()` returns exactly the probes that are LEFT BEHIND: a `mem01_probe_*` database is stale ⟺ it has NO live backend in `pg_stat_activity` AND (its `mem01_probe_owner` marker is missing OR the marker's recorded `pid` is not a live process). `released` is irrelevant to staleness (it governs §16.4 reuse only); a markerless probe with a live connection is an in-flight creation, not stale. Exposed as the pure classifier `probe_db.is_stale(marker: ProbeOwnerMarker | None, *, pid_alive: bool, live_connections: int) -> bool` with `ProbeOwnerMarker` a frozen dataclass `(run_id: str, pid: int, created_at: datetime, released: bool)`. | new `test_probe_db_staleness.py`: the classifier over: marker None + 0 conns → stale; marker None + live conns → NOT stale; dead pid + 0 conns → stale (released either way); live pid + 0 conns → not stale (released either way); dead pid + ≥1 conn → not stale. Field set of `ProbeOwnerMarker` asserted, no repr/ordering internals. The actual listing (SQL + pid liveness) is reviewer-checked against the classifier. | fixer A-1 (probe_db.py) |
| A11 | W3-B deviation 2 (`criterion_status` has no `errors` parameter; per-gate `if errors: ERROR` copies) | `gates.context.criterion_status(criterion, *, numerator, denominator, errors=0)`: `errors > 0` → `ERROR` (R2) before any other rule; gates pass `errors=` and carry no local override. | new `test_gate_context_errors.py`: `errors=1` with a passing ratio → `ERROR`; `errors=0` unchanged. | fixer B-3 |

## Group B — house rules / structure (existing seal guards; no new tests)

| id | findings | change |
|---|---|---|
| B1 | #11 (four roster copies, two unguarded) | `criteria.CRITERIA_GATES` and `census_denominators.CENSUS_GATES` import `GATE_NAMES` from `statuses` (no literal copies outside `statuses.py`; `gates/registry.py` keeps its import-time cross-check). |
| B2 | #12 (run-id grammar in four encodings) | One leaf module `run_id.py`: `RUN_ID_PATTERN`, `is_run_id(text) -> bool`, `assert_run_id(text)` (raises `ProbeDatabaseError`/`IntegrityViolationError` as today's callers do), `new_run_id(now)`; `verdict.py`, `probe_conn.py`, `run_identity.py` import it (`run_identity.new_run_id` stays as a re-export — §1.3). |
| B3 | #19, #9 | `criteria.criteria_sha256` → `hashing.sha256_file` (wrapped in `CriteriaError`); `fixtures.digest.fixtures_digest` builds its lines with `hashing.sha256_file` and digests with `hashing.canonical_lines_digest` (value-preserving: prove equality before/after on the current package). |
| B4 | #18, #10, #8 | Delete `IdemScenario.messages_by_ref`; delete the unread `FIXTURES_DIGEST_VERSION` (the tag is emitted from `vis_matrix_a.py`) or alias the live constant to it; `HiddenBudget(create_if_missing=…)`: route `release.py`'s ledger creation through it or delete the parameter — no dead seam either way. |
| B5 | #13, #14, #15, #17 | Docstrings: `hidden_budget.py` `Used by` names the real callers; `corpus_identity.py`/`run_identity.py` list `.db` (`read_alembic_version`) under `Depends on`; `conn: object` → `AsyncSession` under `TYPE_CHECKING` on the §1.3 signatures; `fixtures/vis_matrix.py` raises `FixtureError` instead of `KeyError`. |
| B6 | #16/#29 (read_alembic_version thrice per run) | `db.read_alembic_version` memoises per database name for the life of the process (one global-plane read per database per run); contract §16.15 wording relaxed accordingly. |
| B7 | W3-D F4, F5 | Every `gates/gate_<name>.py` declares `NEEDS_PROBE: bool`; the runner derives its probe-gate set from the flags (no hardcoded `PROBE_GATES`). `exceptions.RunRefusedError(Mem01Error)` for `unknown gate: <name>`, `no scorable hidden set`, `partial run (--gates) is not an acceptance path`, out-of-order step. |
| B8 | ruff | `ruff --fix` the three I001 fixture files; `ruff format tools/mem01_verify` (27 files; the 50 sealed test files are already formatted). |
| B7-rest | fixer B-3 report | `runner_output.RunState._require` (out-of-order step guard) still raises `IntegrityViolationError`; the group-A fixer owning `runner_output.py` swaps it to `RunRefusedError`. Contract §1.3 exception table gains `RunRefusedError` in v1.2.8. |

## Clean-gate rule learned during group B

Two sealed CLI tests are non-deterministic ONLY under concurrent probe activity on the same server:
`test_both_invocation_forms_produce_the_same_block` (A12 fixes its cause) and
`test_verify_step1_refusals.py::test_a_probe_held_by_a_foreign_connection_cannot_be_dropped_and_the_run_aborts_at_step_11`
(the test holds "the probe that appeared while the run ran" — with several agents creating probes it can
hold a FOREIGN probe, the run's own probe drops fine and a verdict is printed). Both passed in every
isolated re-run. Rule: the §14 clean-gate oracle run is executed ALONE on the server (no fleet, no
author run in parallel); a fleet-time failure of either test is re-run in isolation before it counts.

## Group C — contract v1.2.8 (determinations, no code)

- §16.16: the widened `env_allowlist` names (libpq/asyncpg: `PGGSSLIB PGKRBSRVNAME PGSERVICE PGSSLCERT PGSSLKEY PGSSLMAXPROTOCOLVERSION PGSSLMINPROTOCOLVERSION PGSSLMODE PGSSLNEGOTIATION PGTARGETSESSIONATTRS SSLKEYLOGFILE`; platform/child: `PATH SYSTEMROOT SYSTEMDRIVE COMSPEC WINDIR TEMP TMP USERPROFILE HOMEDRIVE HOMEPATH HOME APPDATA LOCALAPPDATA PROGRAMDATA VIRTUAL_ENV UV_CACHE_DIR UV_PROJECT_ENVIRONMENT LANG LC_ALL`), the `.venv`/`__pycache__` observer exclusion, the child-environment rule.
- Bless: `diagnostics["run"]` (stale probes, probe name, observer offenders) beside the 17 gate keys; the two protected-result writes (step 10 and after step 11); unconditional stale-probe listing with conditional preflight; `person_alias.source` in the §5.2 column list (#30 — superset kept, contract amended); `read_alembic_version` "once per database per run"; async `evaluate`/`evaluate_all`; determinations D1–D5 of `gates/context.py`; `GateResult.exclusions`.
- Stage C carry-forwards (recorded, not built): `acceptance_state: validated` on `--validation` entries (#21 refuted as inert in Stage A); `--principal` for validation admissions (#5); the `<SET>: GATES FAIL` line for an `incomplete` H gate under stdout projection (W3-D F6a); IDENT "versioned normalization key" needs a schema column (W3-C — founder decision).

## Codex cross-vendor review of seal amendment 3 (2026-09-07 ~03:40) — reconciled

Verdict as received: NOT SOUND (8 MAJOR, 3 MINOR). Each finding was re-verified against the test
source by the orchestrator before it reached the oracle author.

| # | file:line | Codex claim | verdict | action |
|---|---|---|---|---|
| 1 | `test_probe_child_env.py:100` | `"oneai" not in child.values()` rejects the allowlisted `POSTGRES_USER` | CONFIRMED | assert a non-allowlisted parent sentinel is absent instead |
| 2 | `test_hidden_budget_display.py:81` | vacuous for A4 (feeds all four digests itself) | CONFIRMED | A4 row rewritten: seal `runner_steps.hidden_display_digests` |
| 3 | `test_probe_db_staleness.py:33` | truth table contradicts A12's WHAT | CONFIRMED (registry's own contradiction) | A12 rule corrected: stale ⟺ no live backend AND (no marker OR dead pid) |
| 4 | `test_verify_step1_frozen_expect_lock.py:96` | absent target DB does not prove "no database access" | QUALIFIED | refusal run with an unreachable port when the harness allows it |
| 5 | `test_roster_hidden_subset.py:76` | glob-then-filter passes with NF absent | QUALIFIED (minor) | add NF present-but-non-JSON variant |
| 6 | `test_observer_scope.py:88` | does not detect the runner's suspension | QUALIFIED | A1(d): mechanical guard `not hasattr(runner_steps, "declared_boundary")` + reviewer check |
| 7 | `test_result_block_aborted_projection.py:143` | builder omits `split_evaluated`; raw-print branch unsealed | CONFIRMED in full — the orchestrator's first ruling ("REFUTED, `runner_output.py:291` emits it") was WRONG: that line is inside `build_completed_block`; `build_aborted_block` (`runner_output.py:358`) never emits `split_evaluated`, so the A2 projection collapses nothing on an aborted hidden block. Re-found by both group-A lenses (2026-09-07 ~06:55). Tracked as row A2-b below. | A2-b |
| 8 | `test_app_logging_capture.py:52` | descendant handlers / non-propagating loggers leak | CONFIRMED | add the descendant cases + restoration |
| 9 | `reference.py:155` | oracle harness child env inherits the parent | REFUTED (sealed harness, not the instrument) | none |
| 10 | `test_probe_db_staleness.py:98` | dataclass representation not declared | QUALIFIED | `ProbeOwnerMarker` shape declared in §16.16(l); fields only |
| 11 | `test_gate_context_errors.py:71` | positional `errors` not caught | CONFIRMED (minor) | `status(replay, 1, numerator=0, denominator=1000)` must raise |

Unsealable-in-Stage-A wiring, recorded for reviewer checks (hidden runs abort at step 6 before
any admission or reservation, so the CLI path cannot reach them): A4 display wiring, A6 sealing of
an admitted attempt, A7 event paths on hidden runs, A10 failed-child streams, A1(a) the real
Alembic child's environment (the pure builder is sealed; the call site is reviewed).

## Group A review (2026-09-07 ~07:00) — two lenses, 16 findings, adjudicated, reconciled by the orchestrator

Fleet result before review: 601/601 green (workflow `wf_a79a2285-fe8`). Verdicts below are the orchestrator's after the
adjudicators' evidence (the critical one re-verified by hand: `runner_output.py:358` `build_aborted_block` never emits
`split_evaluated`; only `build_completed_block:298` does).

| # | file:line | claim | verdict | action |
|---|---|---|---|---|
| 1, 10 | `runner_output.py:368` | aborted block omits `split_evaluated` → hidden projection collapses nothing | CONFIRMED critical (both lenses) | **A2-b** |
| 2 | `result_block.py:174` | `hidden_gates` derived from the criteria list, not the H-split roster | QUALIFIED major (mechanism holds; real trigger: an H gate with no entry on the evaluated split keeps its reason) | **A13** |
| 3 | `verify_step1.py:281` | projection-failure fallback printed unvalidated | CONFIRMED major | **A16** |
| 4 | `run_identity.py:301` | `__pycache__` exclusion is suffix-blind AND `code_hash` drops every file under `__pycache__` (a non-bytecode file there is neither hashed nor observed) | QUALIFIED major (attribution corrected; the hashing hole is the stronger half) | **A14** |
| 5 | `run_identity.py:302` | `.venv` exemption justification overclaims (`uv.lock` pins declared deps, not venv bytes) | QUALIFIED minor | docstring D1 |
| 6 | `probe_db.py:302` | stale listing opens a session on each 0-backend foreign probe → can block a concurrent FORCE-less drop for an instant | QUALIFIED minor (window real; "move after the early return" contradicts §16.16(n)) | folded into **A15** |
| 7 | `probe_db.py:250` | a markerless in-flight probe (CREATE→migrate, and migrate→claim) classifies stale — seconds-long windows | QUALIFIED minor, real | **A15** |
| 8, 13 | `verify_step1.py:186` | `capture_app_logging` skipped when artifacts are unwritable → app records reach stderr in steps 4–6 | QUALIFIED minor | **A20** |
| 9 | `runner_logging.py:119` | only the `app` hierarchy is captured; other loggers reach stderr via lastResort | QUALIFIED minor, hardening | carry-forward C1 |
| 11 | `verify_step1.py:345` | `cleanup_failed` also fires on an artifact-write failure | QUALIFIED minor | **A18** |
| 12 | `verify_step1.py:326` | free-repeat path discards the projection abort → wrong exit code | CONFIRMED minor | **A19** |
| 14 | `runner_steps.py:217` | a failing Alembic child writes `probe_migration.log` under an ABSENT hidden root (§16.14 forbids the tree) | CONFIRMED major | **A17** |
| 15, 16 | docstrings | `probe_db` "Used by" names a non-importer; `runner_steps` omits `.statuses` | CONFIRMED minor | docstrings D2, D3 |

### Group A, round 2 (sealing test first where the WHAT is pure — amendment 4 = `test_review_round_a.py`)

| id | WHAT | seal | fix owner |
|---|---|---|---|
| A2-b | `build_aborted_block` emits `split_evaluated` on every run (never null on hidden run kinds: it is known from the options at step 1); `check_aborted` under `projection="stdout"` REJECTS an aborted block of a hidden run kind that lacks it. | amendment 4 (p) + existing sealed CLI refusal tests force the runner | F-schema (`result_block_schema.py`) + F-runner (`runner_output.py`) |
| A13 | Hidden-gate reduction is roster-based: on a hidden run kind every `H_SPLIT_GATES` gate projects to `{status}` (and the schema requires it) whether or not it has an entry on the evaluated split; `hidden_gates = frozenset(H_SPLIT_GATES) | emitted` in both `project_for_stdout` and `check_gates`. | amendment 4 (q) | F-schema (`result_block.py`, `result_block_schema.py`) |
| A14 | `__pycache__` rule: the observer exempts ONLY `.pyc`/`.pyo` files under a `__pycache__` directory; any other file there is an ordinary path that follows the normal closure test — inside the closure (e.g. `backend/app/__pycache__/oracle.json`) it is NOT an offender and, being inside the editable scope, IS hashed by `code_hash` (the scope walk excludes ONLY `*.pyc`/`*.pyo`); outside the closure (e.g. `backend/scripts/__pycache__/oracle.json`, `docs/__pycache__/x.json`) it IS an offender. A file that is in `code_files` can never be an offender. (The first amendment-4 brief wrongly asked for an in-scope file to be an offender; the oracle author caught the conflict and the test was corrected before the hash was recorded.) `.venv` stays fully exempt (§16.16(c)), with the docstring corrected: "pinned by `uv.lock`" means the declared dependency set is part of `code_hash`, not the venv bytes. | amendment 4 (r) | F-schema (`run_identity.py`) |
| A15 | The ownership marker is written BEFORE the migration child runs: immediately after `CREATE DATABASE` the creator opens the probe, creates `mem01_probe_owner` and inserts `(run_id, pid, now, released=false)`, then migrates, then verifies emptiness EXCLUDING the marker table; a markerless probe therefore means "never claimed". The stale listing reads a marker only for probes with 0 live backends through a connection that lives for the single SELECT; the residual instant in which that read can collide with a concurrent FORCE-less drop is accepted and recorded in §16.16(s). | reviewer check (needs a live probe); existing `test_probe_db.py` + `test_verify_step1_probe_reuse.py` stay green | F-probe (`probe_db.py`, `probe_env.py`) |
| A16 | The fallback after a stdout-projection rejection is a MINIMAL aborted block (envelope + `reason` + `aborted_at_step` + `split_evaluated` + null identity + `cleanup`, no gates/criteria/diagnostics), itself validated under `projection="stdout"`; if even that is refused the runner prints only the line `MEM01 INTERNAL ERROR: <violation class>` and exits 2 (no block, no verdict). | reviewer check | F-runner (`verify_step1.py`) |
| A17 | `migration_log` is passed only when `artifacts_writable(state, hidden_root)`; otherwise `None` (maintenance-cwd fallback, §16.16(a)) — nothing is ever written under an absent hidden root. | reviewer check | F-runner (`runner_steps.py`) |
| A18 | An artifact-write failure at step 10/11 is its own abort reason `artifacts_unwritable: <exception class>` (step 11, exit 2, no verdict); `cleanup_failed` is reserved for a failed drop; when both happen the drop failure wins the reason and the write failure is listed in `diagnostics["run"]["artifact_write_failure"]`. | reviewer check | F-runner (`verify_step1.py`, `runner_logging.py`) |
| A19 | The free-repeat path binds the projection abort like the normal path: a rejected projection → the fallback of A16 and exit 2. | reviewer check | F-runner (`verify_step1.py`) |
| A20 | `capture_app_logging` is held from step 4 to step 11 on EVERY run; when no report directory may be written (absent hidden root) the records go to an in-memory sink that is discarded, never to stderr. | reviewer check | F-runner (`verify_step1.py`, `runner_logging.py`) |
| D1–D3 | docstrings: `run_identity` `.venv` justification; `probe_db` "Used by" = `runner_steps` (and the oracle); `runner_steps` "Depends on" adds `.statuses`. | — | the fixer owning each file |
| C1 | carry-forward: capture the ROOT logger (all hierarchies) from step 4 to 11 so no third-party logger reaches stderr; decide in Stage B whether `logging.lastResort` output is an R5 concern. | — | contract note |

### Round-2 seal dispute (fixer F-schema, 2026-09-07 ~07:25) → seal amendment 5

`test_result_block_aborted_projection.py:151` (amendment 3) derives the hidden-evidence gates of its hand-made
stdout shape from the criteria entries on the evaluated split, so its positive control keeps
`{"status": "skipped", "reason": "aborted"}` on LANG/RET; §16.16(q) (amendment 4) requires every
`H_SPLIT_GATES` gate to reduce to `{status}` and the validator now enforces it — the two seals cannot both
hold (verified by the orchestrator at the source). The older seal encodes the superseded criteria-derived
reading, so amendment 5 changes that ONE helper line to the roster rule (hand-spelled roster), reverse-hash
proven by the oracle author; strictly less reaches stdout, no R5 downside. Round-2 file sizes above the 400
target but under the 500 ceiling: `result_block_schema.py` 409, `run_identity.py` 418, `probe_db.py` 425 —
carried to the house-rules pass (candidate splits: `probe_marker.py`, `closure_scope.py`).

**Second collision (fixer F-runner, ~07:50) → seal amendment 6.** Three ORIGINAL sealed CLI files
(`test_verify_step1_cli.py:49-52`, `test_verify_step1_frozen.py:132-135`,
`test_verify_step1_frozen_expect_lock.py:50-53`) assert `reason == "aborted"` on EVERY gate of an aborted
hidden block printed on stdout; under (q) — as sealed by amendment 4, which reduces skipped H gates too — the
four H-split gates print `{"status": "skipped"}` with no reason (5 sealed tests, `KeyError: 'reason'`).
Ruling: (q) stands, whatever the gate's status; the abort cause lives in the block's `aborted`/`reason`/
`aborted_at_step`. Amendment 6 replaces those three assertions with the precise shape (all gates skipped;
non-H gates carry `reason == "aborted"`; H gates carry no reason). The round-2 workflow was STOPPED after
wave 2 (F-runner green on its 80 tests, 8 failures = the 5 above + 3 (q) tests of `test_review_round_a.py`
pending the projector fix that has since landed); it resumes with a fresh oracle run, lens and adjudication
once amendment 6 is recorded. Round-2 runner sizes above target: `verify_step1.py` 436, `runner_output.py`
418, `runner_steps.py` 409 — house-rules pass.

**Resumed fleet, fresh oracle run (~08:17): 610/613.** The three failures were `test_review_round_a.py::
test_every_h_split_gate_projects_to_status_only…[completed_validation|aborted_checkpoint|aborted_validation]`.
The fleet's fixer fixed the first at the root (§3.4: `pending` `.validation` entries keep their full form even
when the evaluated split is `validation` — the projector collapsed every entry on that split and the validator
required it; now ONE shared predicate `result_block_schema.is_collapsible(entry, split_evaluated)` = "a SCORED
entry on the evaluated split" is used by both, so they cannot drift) and disputed the other two, verified by
the orchestrator: the test's own aborted builder (line 106) gives every non-QS/SNAP gate `{status, reason}`
with no `criteria` key, while the non-H control at line 232 asserts `{status, reason, criteria}` —
unsatisfiable without fabricating ids. **Seal amendment 7**: the control becomes shape-agnostic (`projected
["gates"]["CH"] == block["gates"]["CH"]`, non-H gates pass through unchanged). The fleet was stopped again
before a wasted fix round and resumes on a fresh oracle run once amendment 7 is recorded.

## Round 2 review (2026-09-07 ~09:10) — one lens, 4 findings, adjudicated

Fleet result before review: 613/613 green (run `wf_7542d159-b90`, post-amendment-7). Orchestrator verdicts after
the adjudicators' evidence:

| # | file:line | claim | verdict | action |
|---|---|---|---|---|
| 1 | `verify_step1.py:386` | the stdout-projection refusal is discovered AFTER the hidden ledger / validation journal record the outcome → a refused run is recorded as completed / verdict-reserved | CONFIRMED major (unreachable in Stage A: hidden runs abort at step 6; real from stage C) | **A21** |
| 2 | `runner_output.py:415` | the "minimal" fallback carries the 17 skipped gate placeholders and an empty `criteria` list, which §16.16(t) said it must not | QUALIFIED minor — the code follows §3.3 (an aborted block always carries the gate map); (t)'s parenthetical was shorthand for "no measurement" | contract (t) reworded (**C2**) |
| 3 | `runner_logging.py:243` | `write_guarded` degrades only `OSError`; any other exception from an artifact write or a render escapes as a traceback with exit 1 | CONFIRMED major (no live trigger today; three spec-named invariants — (t), §3.5 exit 2, R11) | **A22** + sealing test (amendment 8) |
| 4 | `verify_step1.py:359` | the free-repeat transcript write is gated on `report_dir is not None`, the only write site not using `artifacts_writable` | QUALIFIED minor (uniformity; the root is proven at step 6 and the tree already exists) | **A23** |

### Group A, round 3 (after the mutation check; the same runner files are being mutated/restored until then)

| id | WHAT | seal | fix owner |
|---|---|---|---|
| A21 | The printed shape is DECIDED before any outcome is recorded: `printed = project_for_stdout(block)` and its stdout validation run first; a refusal folds into `abort` (step 12, reason `internal projection error: <class>`) BEFORE `_record_hidden_outcome` and the artifact writes, so the ledger/journal record an ABORTED outcome (no verdict reserved) and `_print_block` only emits the already-decided lines. | reviewer check (Stage-C path) | F-runner-3 (`verify_step1.py`) |
| A22 | `write_guarded` degrades ANY exception (`except Exception`, R5: class name only) to the `artifacts_unwritable: <class>` detail; the two `render_result_block` calls in `_print_block` are guarded the same way, falling through to `MEM01 INTERNAL ERROR: <class>` + exit 2 — a runner never dies with a traceback (R11, §3.5). | amendment 8: `test_write_guarded_degrades_any_exception.py` — `write_guarded(<raises TypeError>)` returns `"TypeError"`; `write_protected_result(dir, {"a": datetime})` inside `write_guarded` returns `"TypeError"` and leaves no partial file; an `OSError` still returns its class name | F-runner-3 (`runner_logging.py`, `verify_step1.py`) |
| A23 | `_finish_free_repeat` gates its transcript write on `artifacts_writable(state, hidden_root)` like every other write site (thread `hidden_root` in). | reviewer check | F-runner-3 (`verify_step1.py`) |
| A24 | (from the mutation check, M17 UNCAUGHT) `hashing.merkle_sha256`'s DEFAULT exclusions follow §16.16(r): only `*.pyc`/`*.pyo` are excluded, no directory exclusion — a non-bytecode file under `__pycache__` changes `merkle_sha256`, hence `runner_sha256` and `fixtures_digest` (the same undeclared-channel hole A14 closed for the closure). `exclude_dirs` stays as an explicit parameter for callers; its default becomes empty. | amendment 8, second file `test_merkle_pycache_rule.py`: planting `__pycache__/oracle.json` under a tree CHANGES `merkle_sha256`; planting `__pycache__/a.cpython-312.pyc`, `sub/__pycache__/x.pyo` or `c.pyc` does NOT; existing `test_hashing.py` stays green (it uses bytecode only). | F-runner-3 (`hashing.py`) |
| C2 | §16.16(t) reworded: the minimal fallback is the §3.3 aborted shape with NO measurement — envelope, `reason` (violation class only), `aborted_at_step`, `split_evaluated`, null identity, `cleanup`, the 17 `{"status": "skipped", "reason": "aborted"}` gate placeholders (reduced to `{status}` for H gates on hidden kinds by the projection) and an empty `criteria`; no diagnostics. | — | contract |

### Round 3 result (fixer F-runner-3, ~10:47)

A21–A24 implemented; 96/96 on the named modules, no sealed dispute. Two rulings from its report: (1) the contract
sentence "hence `fixtures_digest`" was wrong — `fixtures_digest` is by design a `.py`-only digest of the fixture
source modules and never goes through `merkle_sha256`; a smuggled non-bytecode file under `fixtures/` is covered
by `runner_sha256` (the fixtures tree is inside the runner folder) — §16.16(r) corrected; (2) a new abort reason
`internal render error: <class>` (protected result only; stdout shows only the `MEM01 INTERNAL ERROR` line) —
blessed in §16.16(t). Side effect: `verify_step1.py` is now 495 lines (5 under the CI ceiling) — the house-rules
split (HR-runner → `runner_cleanup.py`, `runner_render.py`, `runner_probe.py`) is MANDATORY before the clean gate.

## Round 3 review (2026-09-07 ~11:15) — one lens, 13 findings, adjudicated (11 hold, 2 relocated)

All on one theme: the R11 guarantee ("a run never dies with a traceback; every instrument error is exit 2") is
enforced at individual call sites, so each unguarded site is a hole. Confirmed/qualified sites: `_record_hidden_outcome`
(#2 major), the verdict-line render + audit append (#3 major), `runner_sha256()` before the try (#4 major),
import-time configuration failures — REPRODUCED: `POSTGRES_PORT=not_a_number` → traceback, exit 1, mojibake
stderr (#13 major, relocated from `main()` to import time), the fallback built outside its guard (#11), the
last-resort line's own write (#12), a render failure after the outcome is recorded (#1). Separate: CPython's
`*.pyc.<id>` temp files are admitted by the suffix-only rule → `runner_sha256` can differ within a session
(#5 major); §3.10 text still says directories are excluded (#6 doc); `release_manifest.is_manifested` still
directory-excludes (#10); free-repeat `aborted_at_step` never set to 12 (#7); `_drop_probe` narrows to `Mem01Error`
(#8); `close_app_log` and the drop share one `finally` (#9).

### Group A, round 4 (merged into the house-rules split fleet — same files)

| id | WHAT | seal | fix owner |
|---|---|---|---|
| A25 | ONE R11 guard. The entry point (`main()` / the `__main__` shim) first forces UTF-8 on stdout and stderr, then imports the runner LAZILY inside a single `try` that encloses everything up to process exit: any exception anywhere — import-time configuration failures, `runner_sha256()`, the ledger/journal writes, renders, the stdout writes — becomes a best-effort `MEM01 INTERNAL ERROR: <exception class>` line (its own write failure suppressed) and exit 2; no traceback ever reaches stderr; `runner_sha256()` moves inside the step-2 try so its failure is an ordinary aborted run. Rendering (block text, per-SET lines, verdict line) is computed into strings INSIDE the pre-record decision (`StdoutDecision` carries them), so a render failure folds into `abort` before the ledger/journal record anything; the ledger/journal writes themselves, if they raise, end the run through the single guard (the durable state is whatever was written — recorded in §16.16(t)). | amendment 9 `test_entry_guard.py`: the SCRIPT form run with `POSTGRES_PORT=not_a_number` (child env otherwise clean) exits 2, its stdout's last line is `MEM01 INTERNAL ERROR: ValidationError`, stderr contains no `Traceback`; the same with `APP_ENV=production` → `MEM01 INTERNAL ERROR: InsecureConfigurationError`. | HR-runner |
| A26 | Bytecode rule (as SEALED by amendment 9, which corrected this row's first wording "any element of `path.suffixes`" — that would have made `notes.pyc.txt` bytecode): drop CPython's purely numeric temp-tail suffixes, then the last remaining suffix must be `.pyc` or `.pyo` (`x.cpython-312.pyc.140213`, `y.pyo.99` → bytecode; `notes.pyc.txt`, `a.py`, `__pycache__/oracle.json` → not); this one predicate (`hashing.is_bytecode(path)`) is used by `merkle_sha256`'s default, `run_identity`'s scope walk and observer exemption, and `release_manifest.is_manifested`; no directory exclusion anywhere; `oracle.json` under `__pycache__` stays hashed/manifested/observed. | amendment 9 `test_bytecode_suffix_chain.py`: `x.cpython-312.pyc.140213` and `y.pyo.99` are excluded by `merkle_sha256`, by `build_closure`'s `code_files` and by `is_manifested`, while `__pycache__/oracle.json` is included by all three. | HR-pis (`hashing.py`, `run_identity.py`, `release_manifest.py`) |
| A27 | Cleanups: `_finish_free_repeat` sets `state.step = PRINT_STEP` before its decision; `_drop_probe` catches `Exception` (class name only) so a driver/OS error becomes `cleanup_failed`; `close_app_log` is isolated from the drop (`try/finally`). | reviewer check | HR-runner |
| C3 | Contract: §3.10 `runner_sha256` definition and §16.6 manifested-files rule reworded to the bytecode rule; §16.16(t) gains the single-guard sentence and the ledger-write consequence; §1.3 `hashing` row gains `is_bytecode`. | — | contract (done by the orchestrator) |

Round-4 + split result (~12:05): HR-runner 95/95 and HR-pis 145/145 on their modules, no dispute; every file under
400 lines (`verify_step1.py` 495 → 390). Layout decisions the fixers took beyond the brief, all re-exported so
§1.3 and the seals still resolve: `protected_result_relpath` lives in `runner_logging.py` (re-exported from
`runner_output`); `selected_gates` + `UNKNOWN_GATE_REASON` in `runner_steps.py`; `HIDDEN_RUN_KINDS` in
`runner_output.py`; `StdoutDecision`/`decide_stdout`/`PROJECTION_REASON`/`RENDER_REASON` in `runner_render.py`;
new siblings `runner_cleanup.py`, `runner_render.py`, `runner_probe.py`, `probe_marker.py`, `input_observer.py`,
`result_block_checks.py`. `merkle_sha256` matches explicit `exclude_suffixes` against the effective suffix too
(so `foo.log.123` is excluded for `{".log"}`) — one rule, no divergent copy.

Post-split oracle run (~12:23): 633/633 green, no probe left; BUT `scripts/check_file_size.py` (the CI gate)
reports four FIXTURE data modules over the 500 ceiling — `ident_cases_a.py` 655, `ident_cases_b.py` 678,
`time_cases.py` 523, `vis_matrix_b.py` 555 — all modified 05:36 today (a formatter expanding long data
literals). Fixed by a dedicated data-only split (new letter-suffixed siblings, aggregated tuples identical,
proven by a sha256 over `repr()` of every public battery before/after; `fixtures_digest` changes as expected).
Recorded debt under the ceiling but over the 400 target: `hidden_budget.py` 413, `snapshot.py` 451,
`gates/gate_vis.py` 456, `fixtures/red_cases_b.py` 441, `fixtures/vis_matrix.py` 404 — Stage B house-rules item.

## Round 4 / split review (2026-09-07 ~12:45) — one lens, 5 findings, adjudicated → polish pass

Fleet result before review: 633/633 green on the 66-file seal, every runner file under 400 lines. Findings and rulings:

| # | file:line | claim | verdict | action |
|---|---|---|---|---|
| 1 | `lock.py:183` | stage-2 `_unmanifested_files` has no bytecode clause, so it disagrees with `is_manifested` (a planted `.pyo`/temp name in a frozen release would be an "extra") | QUALIFIED major (unreachable in Stage A: `--freeze` refused) | polish P1: apply `is_bytecode` there too |
| 2 | `fixtures/ident_cases_b.py` (+ `ident_cases_a`, `time_cases`, `vis_matrix_b`) | over the 500 ceiling | QUALIFIED major | data-only split (in progress) |
| 3 | `release.py:85` | the cut's copy filter is still pattern-based (`__pycache__`, `*.pyc`) | QUALIFIED trivial | polish P2: ignore callable on `is_bytecode` |
| 4 | `runner_logging.py:12` | "Depends on" omits the lazy `.validation_guard` import | CONFIRMED minor | polish P3: docstring |
| 5 | `runner_output.py:66` | block vocabulary duplicated vs `result_block_schema` (only `HIDDEN_RUN_KINDS` unguarded, fail-open) | QUALIFIED minor | polish P4: import the four names from `result_block_schema`, re-export |

Polish result (~13:55): P1–P4 done — `lock._unmanifested_files` skips bytecode via `is_bytecode`; `release._replace_tree`
uses an `_ignore_bytecode` callable (a `def`, since ruff E731 forbids the lambda); `runner_logging` names `.validation_guard`
lazily; `runner_output` imports `SCHEMA_NAME`/`PHASE_NAME`/`SPLIT_FOR_RUN_KIND`/`HIDDEN_RUN_KINDS` from
`result_block_schema` and re-exports them (same objects, values unchanged). 95/95 on its eight modules; fixture split
7 new siblings, all batteries identical. Clean-gate checks (~14:05): ruff check + `ruff format --check` clean over 172
files, `scripts/check_file_size.py` OK, 103 modules import in a fresh interpreter, seal 66/66, no probe.

## Clean gate (§14, 2026-09-07 13:59–14:14, orchestrator's own run, ALONE on the server)

`POSTGRES_HOST=localhost POSTGRES_PORT=5432 uv run pytest tests/tools --no-cov -p no:cacheprovider -q -rfEs` from
`backend/` → **633 passed, 0 failed, 0 skipped, exit 0** in 14:42; no `mem01_probe_*` database left; seal 66/66;
ruff check + format clean; size gate OK; 103 modules import cleanly. Log: session scratchpad `clean_gate_run.txt`.
The draft release cut and the before/after-census baseline pair on the dev corpus follow (`baseline_runbook.sh`).

## Baseline pair on the dev corpus (§13/§16.12, 2026-09-07 14:15–14:19, org `d1500000-…-000000000001`)

Draft release `step1-gold-v1` cut at `One AI/Benchmarks/_mem01_gold/releases/` (lock `d97cc70c…` before the
instruments, `51dfe0a0…` after the re-cut; runner `fc4610d0…`; 70 visible files verified at the first cut).

| run | verdict | exit | duration |
|---|---|---|---|
| before-census (`20260907t111537z_0392accf`) | `STEP1 TUNING: 1/17 PASS \| provisional=4:FID,THR,IDENT,ATTR \| directional=-` | 2 (ERROR: 5 FAIL, 11 incomplete) | 102 s |
| after-census (`20260907t111804z_4eca5a96`) | identical gate statuses; `code_hash`, `corpus_digest`, `text_digest`, `migrations_digest`, `fixtures_digest` identical | 2 | 88 s |

Gates: SNAP PASS (14,636/14,636 replay-equal); FAIL — COV (`required_logical_delivered` 416/9,124), FID (34/124),
IDENT (`alias_resolution` 22/22 unmerged, `c_normalization_key` 702/18,728; zero false merges), RED
(`no_under_redaction` 120/120; over-redaction 0/110), TIME (`fixtures` 15/66; header comparison 0/5,893);
`incomplete` — QS CH NF LANG IDEM VIS ERASE RET THR ATTR EMB (absent components or Stage-B evidence); LANG's
`no_invalid_states` 8,743/8,743 (language is NULL everywhere). 5,223 exclusions by MIME property; observer clean
(`opened_outside_closure == []`); probes dropped; corpus untouched (0023; 5,893 / 8,454 / 839 / 5,893 after).
Instruments: CENSUS_V1 (`emails_language_null` 5,893; `emails_content_language_present` 4,378; dedup groups 0;
`attachments_with_text` 2,850 of 8,454; `attachments_hash_dup_groups` 704; persons 839 = person_emails 839, no
multi-address person; one grant holder); LANG_BOOTSTRAP_V1 (bg 2,959 · en 1,419 · none 1,515; coverage 74.3%);
LEAK_GROUPS_V1 (1,698 groups; largest 2,684; 62,770 sibling edges; 10 collision edges; review-trigger hashes listed).
Logs: session scratchpad `baseline/`.

## Mutation check (§14, 2026-09-07 09:02–09:13 pass 1; pass 2 = full-command samples for M01 and M10)

Run by the oracle author against the green instrument (613/613), one mutation at a time, each applied by an
exact-match edit, tested with its named sealed module(s) under the oracle flags, and restored byte-exact (sha
before == sha after, all 20; the full 93-file instrument baseline re-verified 93/93 afterwards; `probes after: []`).
Log and per-mutation records: session scratchpad `mutation_pass1.log`, `mutation_results.jsonl`, plan `mutation_plan.md`.

| # | file · mutation | caught by |
|---|---|---|
| M01 | `db.py` R6 snapshot engine `postgresql_readonly: True → False` | `test_db.py`, `test_snapshot.py` (2) |
| M02 | `lock.py` skip the sha256 mismatch for `.jsonl` visible files | `test_lock.py` stage-2 hidden-file seal (1) — a stage-1 tampered-`.jsonl` seal would be a cheap addition |
| M03 | `hidden_budget.py` spend check `>=` → `>` | `test_hidden_budget.py` (3) |
| M04 | `gates/context.py` drop the below-minimum → ERROR branch | `test_gate_context_errors.py`, `test_verify_step1_refusals.py` (2) |
| M05 | `audit_file.py` drop the preset-`event_id` refusal | `test_audit_file.py` (1) |
| M06 | `verdict.py` `GATE_TOTAL` 17 → 18 | `test_verdict.py` (37) |
| M07 | `evid_norm_tables.py` drop U+2028/U+2029 | `test_evid_norm.py`, `test_evid_norm_property.py` (4) |
| M08 | `roster.py` duplicates no longer mismatch | `test_roster.py` (1) |
| M09 | `leakage.py` review trigger `>` → `>=` | `test_leakage.py` (1) |
| M10 | `result_block.py` roster rule dropped | `test_result_block.py`, `test_review_round_a.py`, `test_result_block_aborted_projection.py` (7) |
| M11 | `snapshot.py` `stored_null` always False | `test_snapshot.py`, `test_corpus_identity.py` (3) |
| M12 | `lang_bootstrap.py` no `.lower()` | `test_lang_bootstrap.py` (3) |
| M13 | `statuses.py` PASS on an empty deciding set | `test_statuses.py`, `test_gates_stage_a.py` (6) |
| M14 | `probe_db.py` `is_stale` ignores live connections | `test_probe_db_staleness.py` (4) |
| M15 | `probe_env.py` child env starts from the parent | `test_probe_child_env.py` (4) |
| M16 | `verify_step1.py` prints the raw block | `test_verify_step1_frozen.py` (3) |
| M17 | `hashing.py` `merkle_sha256` default `__pycache__` directory exclusion dropped | **UNCAUGHT** (`test_hashing.py` plants bytecode only) → row **A24**: the directory exclusion itself contradicts §16.16(r); the rule, not today's behaviour, gets sealed by amendment 8 |
| M18 | `run_identity.py` observer exempts everything under `__pycache__` | `test_review_round_a.py`, `test_observer_scope.py` (1) |
| M19 | `result_block_schema.py` `is_collapsible` collapses pending entries | `test_review_round_a.py`, `test_result_block.py` (2) |
| M20 | `runner_steps.py` display digests over two splits | `test_hidden_budget_display.py` (1) |

Pass 2 (full oracle command, 09:31–10:03): M01 → 2 failed / 611 passed; M10 → 19 failed / 594 passed; both
restored byte-exact; the 93-file baseline re-verified after the pass; `probes after: []`.

Note on the sealed harness: `reference.merkle_sha256_reference` (a sealed helper the CLI seals use for the expected
`runner_sha256`) still excludes the whole runner-folder `__pycache__`; it agrees with A24 as long as that
directory holds only bytecode, and a non-bytecode file smuggled there turns the sealed verdict-line test red —
the alarm we want. Left untouched on purpose.

## Refuted / not acted on

- #21 `acceptance_state` hardcoded — inert in Stage A (no validation run can score); carried to Stage C.
- #28 aborted block omits `release_name`/`release_state` — the sealed aborted shape and §16.14 agree (absent-when-not-loaded is what the oracle pins).
- W2-B deviation 3 (`build_closure` hashes the packaged annex, not the release copy) — identical on every cut release; stage-1 lock verification pins the copy; revisit if a release may ever carry a different annex.

## Codex cross-vendor review of the instrument CODE (2026-09-07 ~14:30) — 14 findings, adjudicated, reconciled

Codex (GPT-5.6, max reasoning, read-only) reviewed the final `backend/tools/mem01_verify/` package against the
contract and returned `VERDICT: NOT SOUND` — 12 MAJOR, 2 MINOR (brief and verbatim output: session scratchpad
`codex_brief_instrument_code.md`, `codex_review_instrument_code.md`). Every finding went to one read-only Opus
adjudicator (workflow `wf_79919d97-8c1`; results `adjudication_codex_code.json`), then the orchestrator re-read the
cited source for each. Net: 7 CONFIRMED, 6 QUALIFIED, 1 REFUTED; the surviving 13 are Group A round 5 (rows
A28–A40) under contract v1.2.9 §16.17 (a)–(l).

| # | Codex finding | Codex | Adjudicator | Orchestrator's own check | Row |
|---|---|---|---|---|---|
| 1 | `gates/gate_red.py:184` the RED `logging` collector listens on root; under the runner's capture `app` does not propagate → the surface is empty and scored clean | MAJOR | CONFIRMED, MAJOR (reproduced: 0 records under `discard_app_logging`; the surface is empty even outside the capture — no Stage-A handoff logs on a good input) | `runner_logging._app_hierarchy_through` sets `app.propagate = False` (line 96); `_surface_outputs` exercises only `redact_secrets`/`extract_text`, which never log; the parser's degraded-parse path (`email_parser.py:174`) logs deterministically at nesting depth ≥ 400 (experiment `red_logging_carrier_experiment.py`: 200 → parsed, 400/800/1500 → `failed` + one WARNING with traceback, canary absent) | **A28** |
| 2 | `gates/gate_cov.py:155` a truncated extraction (`extracted_data.truncated = true`) counts as delivered | MAJOR | CONFIRMED, MAJOR (7 real corpus rows) | `_is_delivered` checks status/text/provenance only; read-only corpus query: 7 rows `extracted` + `truncated=true` + `structured-truncated` detail, all delivered-shaped (2,817 delivered-shaped in total) | **A29** |
| 3 | `runner_logging.py:124` a stock `FileHandler` reports emit failures (record + traceback) to stderr | MAJOR | CONFIRMED, MAJOR | plain `logging.FileHandler`; `handleError` untouched | **A30** |
| 4 | `verify_step1.py:384` the entry guard catches `Exception` only: `KeyboardInterrupt`/`SystemExit` escape; cancellation inside `_sequence` skips the cleanup `finally` | MAJOR | QUALIFIED, MAJOR (SystemExit pass-through is by design for argparse; the interrupt half holds) | both guards are `except Exception`; the first `try` in `_run` has no `finally`, so an interrupt in steps 2–9 never reaches `drop_probe` | **A31** |
| 5 | `probe_env.py:144` `asyncio.to_thread(subprocess.run, …)` is not cancellable; a cancelled migration leaves Alembic connected, the FORCE-less drop fails and masks the cancellation | MAJOR | QUALIFIED, MINOR (reproduced; unreachable through the CLI in Stage A) | confirmed shape at `probe_env.py:143-152` | **A40** |
| 6 | `runner_cleanup.py:130` the step-12 rewrite discards `write_guarded`'s failure | MAJOR | CONFIRMED, MAJOR | `rewrite_protected_result` returns None; called after `record_hidden_outcome` | **A33** |
| 7 | `verify_step1.py:366` argparse echoes a rejected `--org` value; `unknown gate: <name>` echoes input | MAJOR | QUALIFIED, MINOR (the unknown-gate half is contract-mandated and sealed; the argparse half holds) | `--org` is `type=UUID` → argparse's default message quotes the value | **A32** |
| 8 | `hidden_budget.py:321` completed protected results are cached beside the VISIBLE ledger (`<gold root>/hidden_budget.jsonl.results/`) | MAJOR | CONFIRMED, MAJOR (latent-critical from Stage B) | `_result_file` = ledger sibling; `runner_steps.py:227` constructs the budget from the release path; `release.py:139` lays the ledger down with no results root | **A34** |
| 9 | `hidden_budget.py:339` a cached result is replayed without checking its digest | MAJOR | QUALIFIED, MINOR | `_load_result` uses the digest as a filename only | **A35** |
| 10 | `validation_guard.py:204` the covered-abort rule counts aborts only; `admit A1 → abort A1 → reset A1 → admit A2 → crash` admits A3 | MAJOR | QUALIFIED, minor in Stage A / major from Stage C | `_require_covered_abort` checks `len(aborts) == 1` and a matching reset only | **A37** |
| 11 | `verify_step1.py:248` the free repeat discards the drop result and replays a PASS with exit 0 | MAJOR | CONFIRMED, MINOR (unreachable in Stage A) | `_finish_free_repeat` does not bind `drop_probe`'s return | **A36** |
| 12 | `verify_step1.py:155` the free repeat returns before `check_observer` | MAJOR | **REFUTED** — §3.2 step 6 says "print … and stop"; §3.11 places the check at step 9; unreachable in Stage A | agreed: contract-prescribed; recorded as a Stage C carry-forward in §16.17(i) | — |
| 13 | `roster.py:123` duplicate EXPECTED ids vanish in the set comparison | MINOR | CONFIRMED, MINOR | `duplicate` counts the present side only; `_is_mismatch`'s docstring claims otherwise | **A38** |
| 14 | `roster.py:74` `errors="replace"` lets invalid UTF-8 inside an opaque field pass | MINOR | QUALIFIED, MINOR (the fix must decode per line, not "strictly" per file) | module docstring claims the opposite of the code | **A39** |

Codex's AGREED items (independent corroboration): R6 snapshot plane, marker-before-migration, hidden projection
whitelist, `read_alembic_version` as the one BYPASSRLS read, the observer window, closure hashing.

### Group A, round 5 (sealing tests first; contract v1.2.9 §16.17; amendment 10 = `test_hidden_budget.py` + the new `test_review_round_5_*.py` files)

| Row | WHAT (§16.17) | Sealing test | Owner |
|---|---|---|---|
| A28 | (a) RED collector on root AND `app`, records formatted like `app.log` (exc text/traceback); (b) positives also travel the parser's degraded-parse carrier (nesting ≥ 400), negatives travel the four text-carrying surfaces only, unexercised Stage-A surface → `surfaces_unexercised`, not scored, `no_under_redaction` incomplete | `test_review_round_5_red.py` + `test_review_round_5_red_b.py` | F5-gates |
| A29 | (c) annex `partial_marker_absent: true`; `structured_truncated` on `ScopedInput`/`CorpusInput` (corpus `extracted_data->>'truncated'`); `_is_delivered` requires it absent; truncated-workbook fixture → `not_ready`; `scope_policy.version` stays `v0` (founder draft) | `test_review_round_5_cov.py` | F5-gates |
| A30 | (d) capture handlers override `handleError` (class name only); `app_log_emit_failures()`; `diagnostics.run.app_log_emit_failures`; `OSError`-derived → `artifacts_unwritable: app_log <class>` abort at step 11; other classes evidence only | `test_review_round_5_logging.py` + `test_review_round_5_logging_b.py` | F5-runner |
| A31 | (e) `_run` turns `KeyboardInterrupt`/`CancelledError` into the abort `interrupted: <class>` and finishes (drop, record, aborted block, exit 2); `main` turns an outer `KeyboardInterrupt` into `MEM01 INTERNAL ERROR: KeyboardInterrupt`; `SystemExit` passes | `test_review_round_5_runner_a.py` | F5-runner |
| A32 | (f) argparse `error()` prints the fixed line `verify_step1: error: invalid usage; see --help`, exit 2 | `test_review_round_5_runner_a.py` | F5-runner |
| A33 | (g) `rewrite_protected_result` returns the class name; performed before the stdout decision; failure → `artifacts_unwritable: <class>` | `test_review_round_5_runner_b.py` + `test_review_round_5_runner_c.py` | F5-runner |
| A34 | (h) `HiddenBudget(…, results_root=None)`; cache at `<results_root>/hidden_budget.results/<sha>.json`; nothing but the sha/path beside the ledger; runner passes the hidden root; no root + completed result → `HiddenBudgetLedgerError` | amendment 10 on `test_hidden_budget.py` + `test_review_round_5_hidden_budget.py` | F5-pure |
| A35 | (h) replay verifies hex64 + sha256 of the cached bytes, else `HiddenBudgetLedgerError` | `test_review_round_5_hidden_budget.py` | F5-pure |
| A36 | (i) free repeat binds the drop result; failure → aborted block (`aborted_at_step: 11`, `cleanup_failed: …`), exit 2 | `test_review_round_5_runner_b.py` | F5-runner |
| A37 | (j) an admission with no terminal event counts as an abort for §3.7(b); reason names the unresolved attempt; `lock_state` unchanged | `test_review_round_5_pure.py` | F5-pure |
| A38 | (k) expected-side duplicates count in `duplicate` | `test_review_round_5_pure_b.py` | F5-pure |
| A39 | (k) per-line strict decode; undecodable line → `malformed` | `test_review_round_5_pure_b.py` | F5-pure |
| A40 | (l) owned `Popen` migration child; killed and reaped on any `BaseException`; timeout still logs | `test_review_round_5_pure.py` | F5-pure |

Owner file sets (disjoint): F5-gates = `gates/gate_red.py`, `fixtures/red_cases_b.py`, `gates/gate_cov.py`,
`release/criteria.step1.v1.yaml`, `fixtures/cov_scenarios_a.py`/`_b.py`; F5-runner = `verify_step1.py`,
`runner_cleanup.py`, `runner_logging.py`, `runner_output.py`, the `RunState` module; F5-pure = `hidden_budget.py`,
`runner_steps.py` (passes `results_root`), `validation_guard.py`, `roster.py`, `probe_env.py`.

## Codex cross-vendor review of the round-5 sealing tests (2026-09-07 ~17:00) — reconciled

The Fable oracle author delivered seven files (`test_review_round_5_{red,cov,logging,runner_a,runner_b,hidden_budget,pure}.py`, 1,494 lines, 41 tests: 33 red today on their ruled surfaces, 8 controls/pins) plus seal amendment 10 (one line of `test_hidden_budget.py`; report: session scratchpad `oracle_round5_report.md`). Codex (GPT-6 Astra, max, read-only; brief `codex_brief_round5_tests.md`, output `codex_review_round5_tests.md`) returned `NOT SOUND` — 11 MAJOR, 2 MINOR. Orchestrator's reconciliation against the source:

| # | Codex finding | Codex | Verdict + evidence | Action |
|---|---|---|---|---|
| 1 | `runner_a.py:120` the interruption seal asserts exactly one `probe.drop()` call, but after the fix both `_finish` (step 11) and `_run`'s `finally` call `drop_probe` | MAJOR | CONFIRMED — `runner_cleanup.drop_probe` calls `probe.drop()` whenever `state.probe is not None` and never clears `state.probe` (lines 77-101); the real drop is idempotent, the stub counts | stub made idempotent; assert the effective drop (`dropped is True`, `drop_calls >= 1`, `lease.exits == 1`) |
| 2 | `logging.py:261` a permanent `lambda: ("OSError",)` lets a runner that reads the failures AFTER `close_app_log` pass | MAJOR | CONFIRMED — the seal never exercises the close-time reset the same file requires | drive `_finish` with a real open capture and an injected emit failure; real accessor + real close |
| 3 | `logging.py:51` OSError subclasses unsealed (`"OSError" in failures` would pass) | MAJOR | CONFIRMED — matches the ruling (builtin OSError family by name; the handler records the most specific builtin class) | `PermissionError` cases in T6 and through `_finish` |
| 4 | `runner_b.py:120` the tuning drive has no reservation/admission, so "rewrite before the outcome is recorded" is not sealed | MAJOR | QUALIFIED — the printed-shape half IS sealed; the order half needs a validation/checkpoint-shaped state | validation-shaped case: failed rewrite → `validation_abort`, never `validation_verdict_reserved` |
| 5 | `cov.py:76` the SQL-spelling pin neither seals the row adapter nor tolerates whitespace | MAJOR | CONFIRMED — `_read_corpus_inputs` could drop the column; §16.17(c) freezes the expression, not its spacing | relaxed regex + a fake session feeding two attachment rows through `_read_corpus_inputs` and `dispose` |
| 6 | `red.py:130` non-empty `logging` output does not prove the degraded-parse carrier ran; only the first positive is checked | MAJOR | QUALIFIED — a spy on `parse_email` binds the output to the handoff; three positives (first, last, beyond-cap) are a sufficient sample given the ~0.3 s cost per carrier | spy asserting raw bytes carry the case text and `parse_status == "failed"` |
| 7 | `hidden_budget.py:174` the traversal test allows "open, hash, then reject" | MAJOR | CONFIRMED — §16.17(h) clarified: a malformed digest is refused before any file is named or opened | cache dir created + `Path.read_bytes` read trap |
| 8 | `hidden_budget.py:100` the tests supply `results_root` themselves; the runner wiring (`runner_steps.py:227`) is unsealed | MAJOR | CONFIRMED | spy on `runner_steps.HiddenBudget` through `admit_hidden_run` (source-text pin as fallback) |
| 9 | `pure.py:249` the test's own `poll()` reaps the child, so kill-without-wait passes | MAJOR | CONFIRMED — §16.17(l) says kill AND reap | subclassed child recording `wait`/`communicate`; `returncode` checked before any test-side poll |
| 10 | `pure.py:236` a stalled loop lets the 30 s sleeper finish and the real function reach the database | MAJOR | QUALIFIED — improbable, but the consequence is a connection attempt at the corpus server | `read_migration_head` trapped; 600 s sleeper; task always cancelled + awaited |
| 11 | `test_hidden_budget.py:172` the amendment passes an absent results root | MAJOR | **REFUTED as a test defect** — ruled in §16.17(h): the budget creates `hidden_budget.results/` (parents included) on first use; the absent-root prohibition of §16.16(t) is the runner's and is enforced at step 6, before the budget exists (the seal now pins that decision) | contract clarified; amendment unchanged |
| 12 | `pure.py:190` `duplicate >= 1` admits `max` instead of the ruled sum | MINOR | CONFIRMED | `== 1` plus a both-sides case `== 2` |
| 13 | `pure.py:207` whole-file rejection passes the `\xff` case | MINOR | CONFIRMED | `present == 2`, `missing == 1`, plus an interleaved two-line case |

Codex AGREED (independent corroboration): the collector assertions, the four negative surfaces, the annex literals, the paired COV inputs, the formatting-error stream assertions, the `--help` pass-through, the exact argparse line, the failed-drop replay assertions, the clean-replay control, both validation sequences, the byte-level amendment proof.

### Codex pass 2 over the strengthened round-5 tests (2026-09-07 ~17:45) — reconciled

After the author's fix round (12 items applied; the sealed set became 11 test files + the helper `review_round_5_harness.py`; 44 red / 8 controls), a scoped second Codex pass (brief `codex_brief_round5_tests_pass2.md`, output `codex_review_round5_tests_pass2.md`) returned `NOT SOUND`: items 1, 2, 8, 9, 10, 11, 12 CLOSED; items 3–7 OPEN through six MAJOR + one MINOR escapes. Orchestrator's verdicts (all accepted → fix round 2, brief `oracle_round5_fix_brief_2.md`):

| # | Codex finding | Verdict + evidence | Action |
|---|---|---|---|
| 1 | `runner_c.py:99` the checkpoint drive builds its budget without `results_root`, so an early `completed` (recorded before the rewrite check) is refused by (h) and, if suppressed, leaves only the later `failed` — the order is not sealed | CONFIRMED — the seal relied on the (h) exception, not on (g) | supply a tmp hidden root; add a clean-completion control |
| 2 | `cov.py:153` boolean fake rows let a text projection (`->> 'truncated'` without `= 'true'`) pass while `bool("false")` disposes untruncated rows `not_ready` | QUALIFIED — the contract's literal is the COMPARISON `extracted_data->>'truncated' = 'true'`; the relaxed regex dropped it | regex tightened to require `= 'true'` |
| 3 | `red.py:171` canary-in-bytes + non-empty output do not tie the logging output to the parser handoff | QUALIFIED — accepted with a capture of the real parser's own records | spy captures the parser logger's messages; one must appear verbatim in `outputs["logging"]`; innermost body == case text |
| 4 | `red.py:141` the spy overwrites `parse_email` on every module unconditionally, replacing a stub adapter that never calls the parser | CONFIRMED | wrap only names that reference the original function |
| 5 | `red.py:150` raw substring matching rejects a base64 body, which the contract does not forbid | QUALIFIED — over-specification | decode the innermost MIME payload; raw match kept as a fast path |
| 6 | `hidden_budget.py:189` the `Path.read_bytes` trap misses `Path.open`/`builtins.open` | CONFIRMED | trap `Path.open` and `builtins.open`; allow ledger + lock |
| 7 | `logging.py:84` in one capture the builtin `PermissionError` supplies the expected tuple, so an ignored vendor subclass is invisible | CONFIRMED (MINOR) | separate captures per class |

Codex AGREED: the helper's states pass the real stdout decision for all three run kinds; the logging runner controls fail with reads after close; the absent-root amendment is valid as clarified; no R5, database/network or instrument-tree write defect found.

### Codex pass 3 (2026-09-07 ~19:40) — reconciled; the test-review loop stops here

After fix round 2 (7 items; the sealed set became 12 new files + the amended one; 45 round-5 reds + 8 controls), a narrow third pass (brief `codex_brief_round5_tests_pass3.md`, output `codex_review_round5_tests_pass3.md`) returned `NOT SOUND`: escapes 1, 4, 7 CLOSED; 2, 3, 5, 6 OPEN. Orchestrator's verdicts (fix round 3 = brief `oracle_round5_fix_brief_3.md`; the loop ends after it — the remaining escapes need a fixer to write `(...)::text` casts, stuff the case text into a MIME preamble or read through `io.open`, and each is either closed below or bounded by the contract):

| # | Codex finding | Verdict | Action |
|---|---|---|---|
| 1 | `cov.py:96` a `::text` cast on the comparison passes the `= 'true'` pin while `bool("false")` disposes untruncated rows `not_ready` | QUALIFIED (contrived, but a one-line tightening) | pin the complete aliased boolean projection `(extracted_data->>'truncated' = 'true') AS structured_truncated` |
| 2 | `red_b.py:130` the whole-message substring fallback accepts the case text in the outer preamble with only the marker in the innermost body | CONFIRMED — the fallback was the orchestrator's own instruction in fix round 2 | fallback removed; decoded innermost body must equal the case text |
| 3 | `red_b.py:101` the recursive decoder rejects valid base64 carriers beyond depth ~900 — "an unstated limit" | **REFUTED as a test defect** — the standard library itself cannot parse a carrier above ~900–1000 levels, so the bound is physical; §16.17(b) now states the carrier depth as 400–900 inclusive (`amend_contract_v129d.py`) | contract bounded; comment in the seal |
| 4 | `hidden_budget.py:178` `io.open` bypasses the `Path.open` + `builtins.open` traps | QUALIFIED — contrived, but the interpreter-level `open` audit event is a strictly better trap | `sys.addaudithook` recorder gated by a flag; ledger + lock allowed |

Codex AGREED: all 13 hashes matched; checkpoint ordering, parser provenance, adapter preservation and the vendor-subclass control hold against in-memory probes; no file modified, no database or network.

## Round 5 result (fleet `wf_6190aed8-4fb`, 2026-09-07 19:50–21:45) and round 6

Three Opus fixers (F5-gates 106/106, F5-pure 86/86, F5-runner 89 cumulative across its batches; no sealed-test dispute) then the fleet's oracle run ALONE: **686 passed, 0 failed, 0 skipped, exit 0** in 19:45; no probe left. Seal 78/78 re-verified afterwards. Fixer deviations accepted and recorded: `_reserve_hidden_budget` gained the `hidden_root` parameter (the construction lives there, not in `admit_hidden_run`); the malformed-digest refusal echoes nothing of the digest and every cache refusal names an exception CLASS, not a path (R5 tightening); `Popen` extracted into a sync helper `_spawn_migration_child` (ruff ASYNC220; the child is observable the moment the coroutine is scheduled); the timeout branch logs the second `communicate()`'s streams (the `TimeoutExpired` buffers duplicate on POSIX and are `None` on Windows); the carrier lives in the new sibling `gates/gate_red_carriers.py` at depth 400 (the contract floor and cheapest value; the whole RED evaluation now costs ~108 s, the four pre-existing surfaces ~68 s of it — accepted, the ~90 s guidance was a target, not a rule); `dispose_with_reason` returned a `not_ready_partial_marker_absent` code (see round 6); `_APP_LOG_FORMAT`/`APP_LOGGER_NAME` restated in `gate_red.py` (see round 6); the step-11 fold lives in `verify_step1._fold_cleanup_abort` with precedence `cleanup_failed` > `artifact_write_failure` > `app_log <class>` (`verify_step1.py` at 457 lines — under the ceiling, above the target: residual debt); `handleError` as a shared mixin `EmitFailureRecordingHandler`; the cleanup-failed free repeat leaves `state.step` at 11.

Review: one functional+security lens, 4 findings, one Opus adjudicator each:

| # | Finding | Adjudication | Orchestrator's ruling | Row |
|---|---|---|---|---|
| 1 | `gate_red.py:125` the §16.9 fragment scan takes the canary's CASE-TEXT span for every surface; on the `logging` surface (a traceback, ~163k chars today; whole-scan limit 200k) a longer log — a longer stdlib path or a raised recursion limit — would leave fragments unscanned while the whole-secret check still passes | QUALIFIED, major (latent: today's log is under the limit and carries no canary; scope wider than stated — every positive loses fragment coverage above the limit) | CONFIRMED as a latent vacuous seal; §16.17(b) amended: non-positional surfaces scan in full | **A41** (sealing test first — round 6) |
| 2 | `hidden_budget.py:463` a tampered/unreadable cache raises out of `reserve` (the run aborts) while §16.17(h) said "charged and re-executed"; every completed result of the pair is loaded, not only the latest | QUALIFIED, minor (the raise is the sealed, registry-mandated behaviour; the contract text is inconsistent) | contract fixed: abort is the rule; latest-only load is a cleanup | **C4** + **B9** |
| 3 | `gate_cov.py:179` `not_ready_partial_marker_absent` is dead code and a third reason-state the fixture model forbids | CONFIRMED, minor | delete the code; count the refused rows in COV `diagnostics["not_ready_partial_marker"]` (§16.17(c) amended) | **B10** |
| 4 | `gate_red.py:92` `_APP_LOG_FORMAT` and `APP_LOGGER_NAME` restated instead of imported (no mechanical guard; the proposed runner import inverts the gates ← runner layering) | QUALIFIED, minor | hoist both names into a leaf module both sides import (`app_log_names.py`); no behaviour change | **B11** |

### Round 6 (A41 sealing test = amendment 11; B9–B11 house rules; C4 contract)

| Row | WHAT | Sealing test | Owner |
|---|---|---|---|
| A41 | `_survives` scans the WHOLE output for fragments on a surface whose output is not a positional transform of the case text (`logging`); the region rule stays for the four text-carrying surfaces | `test_review_round_6_red.py` | F6 |
| B9 | `_free_repeat` loads only the latest completed result of the pair | existing seals | F6 |
| B10 | delete `_PARTIAL_MARKER_REASON` / `_partial_marker_reason`; `dispose_with_reason` returns the exclusion reason only; `diagnostics["not_ready_partial_marker"]` counts the rows the marker refused | existing seals (`test_gates_stage_a` exclusions stay 5) | F6 |
| B11 | new leaf `app_log_names.py` owning `APP_LOGGER_NAME` and `APP_LOG_FORMAT`; `runner_logging` and `gate_red` import them | existing seals | F6 |
| C4 | contract §16.17(b) full-scan sentence, (c) diagnostics attribution, (h) abort wording + latest-only replay (`amend_contract_v129e.py`) | — | done |

## Codex diff-scoped review of the round-5 instrument code (2026-09-07 ~21:50–22:10) — reconciled → round 7

Codex (GPT-6 Astra, max, read-only; brief `codex_brief_round5_code.md`, output `codex_review_round5_code.md`) reviewed the files round 5 changed and returned `NOT SOUND`: rulings (a), (b), (c), (f), (h), (i), (k), (l) IMPLEMENTED; (d), (e), (g), (j) with gaps — 4 MAJOR, 2 MINOR. Orchestrator's verdicts against the source:

| # | Codex finding | Verdict + evidence | Row |
|---|---|---|---|
| 1 | `verify_step1.py:246` `_finish` runs outside the interruption catch; a cancellation during step 11 escapes `main` without an aborted block or outcome; "uncaught at the executable boundary, a traceback" | QUALIFIED, minor — through `asyncio.run` a real Ctrl-C reaches `main` as `KeyboardInterrupt` (caught, no traceback); only a programmatic `Task.cancel()` would surface as `CancelledError`, which `main` (line 448) does not catch. Contract (e) amended: `main` treats `CancelledError` like `KeyboardInterrupt`; finalization after a second interruption stays best-effort | **A45** |
| 2 | `runner_logging.py:196` a capture stream whose `flush()` raises at close: the emit failure is recorded, then `handler.close()` raises again out of `close_app_log` (`stack.close()`, line 247, unguarded) → `MEM01 INTERNAL ERROR: OSError`, no outcome | CONFIRMED, major (disk-full path; the evidence trail is lost) — contract (d) amended: `close_app_log` never raises, returns the class name, folded like an emit failure | **A42** |
| 3 | `verify_step1.py:271` the free-repeat path closes the capture without reading emit failures; a replay with an `app.log` `OSError` prints the recorded PASS, exit 0 | CONFIRMED, minor in Stage A (reachable from Stage C) — contract (d) amended | **A43** |
| 4 | `validation_guard.py:246` the covering reset is compared with the PROPOSED run's candidate, not the aborted admission's: `admit A1(A) → crash → reset A1(B) → authorize B` admits B | CONFIRMED, major (latent, Stage C) — §3.7's "SAME candidate" means the aborted attempt's own; contract (j) amended: reset pair == proposed pair == the admission's pair | **A44** |
| 5 | `verify_step1.py:351` when `cleanup_failed` wins, a rewrite failure is not retained in diagnostics | CONFIRMED, minor | **B12** (retain it in `state.artifact_write_failure` → `diagnostics.run.artifact_write_failure`) |
| 6 | `criteria.step1.v1.yaml` is 1,265 lines, over the 500-line ceiling | **REFUTED** — the ceiling (code-quality A2, `scripts/check_file_size.py`) governs source files; the annex is frozen data whose hash the lock pins (§3.10); splitting it would change every release lock for a rule that does not apply | — |

Codex AGREED: scoped ruff clean; every scoped Python file under 500 lines with the required docstrings; no uncommented `Any`, no `raise Exception`; all 45 COV fixture dispositions; RED captures restore handlers without console leakage; both carriers preserve the complete text; it also re-checked A41, B9 and B10 as closed by the concurrent round-6 fix.

### Round 7 (A42–A44 sealing tests = amendment 12; A45 pin; B12 house rule)

| Row | WHAT | Sealing test | Owner |
|---|---|---|---|
| A42 | `close_app_log(state) -> str | None` never raises; a failing flush/close records the most specific builtin class name and is folded at step 11 exactly like an emit failure (`artifacts_unwritable: app_log <class>` for the OSError family; evidence only otherwise); `_run`'s `finally` tolerates it too | `test_review_round_7_runner.py` | F7 |
| A43 | `_finish_free_repeat` reads `app_log_emit_failures()` (and the close result) before closing; an OSError-family failure prints the aborted block (`aborted_at_step: 11`, `artifacts_unwritable: app_log <class>`), exit 2, nothing recorded; `cleanup_failed` still wins | `test_review_round_7_runner_b.py` | F7 |
| A44 | `_require_covered_abort` requires reset pair == proposed pair == the aborted admission's recorded pair; refusal names the attempt id only | `test_review_round_7_pure.py` | F7 |
| A45 | `main` catches `asyncio.CancelledError` like `KeyboardInterrupt` (`MEM01 INTERNAL ERROR: CancelledError`, exit 2, no traceback) | `test_review_round_7_runner.py` | F7 |
| B12 | a rewrite failure is always retained in `state.artifact_write_failure` (diagnostics) even when `cleanup_failed` is the printed reason | existing seals | F7 |

### Round 6 result (fixer F6, 2026-09-07 ~21:50–22:14)

Rows A41, B9, B10, B11 implemented; the 13 sealed modules of the set (round-6 seal, round-5 RED/COV/hidden-budget/logging seals, `test_app_logging_capture`, `test_hidden_budget*`, `test_gate_scoring`, `test_fixtures`, the probe-backed `test_gates_stage_a`) **124 passed / 0 failed**, exit 0, in 13:35; no probe left; ruff + format clean over the package; size gate exit 0; no dispute. Shape: `_scan_region`/`_survives` take a keyword-only `positional` decided by `surface in _POSITIONAL_SURFACE_NAMES` (= `TEXT_SURFACE_NAMES`), so any surface not declared positional is scanned whole — the safe default; `_free_repeat` loads the latest completed result once; `dispose_with_reason` returns a reason for exclusions only and the COV corpus `counts` gained `not_ready_partial_marker` (present exactly when the corpus was opened — accepted deviation from the "diagnostics" wording: the counts dict IS the gate's diagnostic aggregate); new leaf `app_log_names.py` (28 lines) owns `APP_LOGGER_NAME`/`APP_LOG_FORMAT`, and no gate module imports a `runner_*` module (grep-proven). File sizes: `hidden_budget.py` 478, `gate_red.py` 472, `gate_cov.py` 413, `runner_logging.py` 361 — under the ceiling, above the target (residual debt).

### Round 7 result (fixer F7, 2026-09-07 ~22:20–22:42)

Rows A42–A45 and B12 implemented in `runner_logging.py` (405 lines), `verify_step1.py` (469), `validation_guard.py` (377); the three round-7 seals 13/13 (from 8 red / 5 green), the 17 sibling sealed modules 93 passed, the four CLI modules ALONE 24 passed; ruff + format clean, size gate OK, no probe left, no dispute. Shape: `close_app_log` detaches the capture then wraps `stack.close()` — any `Exception` is classified with the emit-failure rule and returned (a non-`Exception` `BaseException`, i.e. a cancellation, still propagates and is §16.17(e)'s); both runner paths read-then-close through one helper `runner_logging.read_and_close_app_log` and fold the merged, sorted, distinct list through `_fold_cleanup_abort` (`cleanup_failed` > `artifact_write_failure` > `app_log <class>`); the free repeat carries the evidence in the protected block's diagnostics, which the hidden stdout projection drops (printed shape unchanged); `_require_covered_abort` resolves the aborted attempt's own pair through `_admitted_candidate` and requires the reset's and the proposed run's pairs to equal it (refusals name the attempt id only); `main` catches `asyncio.CancelledError`; a failed final rewrite is retained in `state.artifact_write_failure` under `cleanup_failed`. Known residual (pre-existing, recorded): when `cleanup_failed` wins AND the final rewrite fails, `protected_result.json` on disk holds the step-10 block while the printed block carries the step-11 diagnostics — the rewrite is the thing that failed; the diagnostics name the class, so the divergence is visible.

## Final gate after rounds 5–7 (§14, 2026-09-07 22:43 → 2026-09-08 07:39, orchestrator's own runbook, ALONE) and the baseline after round 7

Runbook: session scratchpad `final_gate_runbook.sh`, logs `final_gate/`. Static half at 22:43: `ruff check` + `ruff format --check` clean over `tools/mem01_verify` and `tests/tools`; size gate exit 0; seal **82/82** OK; all **107** instrument modules import in a fresh interpreter. The full sealed suite (`tests/tools`, alone): **705 passed, 3 failed** — the three failures are `test_verify_step1_frozen.py` CLI cases whose subprocess timeout expired with a NEGATIVE value (`timed out after -4515 s`) because the laptop slept from ~22:50 to 07:30 with the suite mid-run (the pytest tree survived and resumed on wake-up); re-run ALONE at 07:45 the module is **5/5 green** in 1:45, so the sealed suite stands at **708/708**. No probe left after the suite. Draft re-cut at 07:36: lock `014a9d9e…` (was `51dfe0a0…`), 75 visible files verified, runner `b1104d53…` (was `fc4610d0…`), fixtures digest `1102bfb5…` (was `95544eed…`; the COV and RED batteries changed). Baseline `after-round-7`, run `20260908t043633z_7bc924c2`, 169 s: `STEP1 TUNING: 1/17 PASS | provisional=4:FID,THR,IDENT,ATTR | directional=- | run_id=20260908t043633z_7bc924c2 | lock=sha256:014a9d9e… | runner=sha256:b1104d53…`, exit 2 (ERROR: 5 FAIL, 11 incomplete, SNAP PASS — the same shape as the 2026-09-07 pair); `code_hash` and `corpus_digest` identical to the after-census run; observer clean; probe dropped; corpus untouched (0023; 5,893 / 8,454 / 839 / 5,893).

What rounds 5–7 changed in the measurement (everything else criterion-identical to the after-census run):

| Criterion | after-census | after-round-7 | Why |
|---|---|---|---|
| `cov.required_logical_delivered` | FAIL 416 / 9,124 | FAIL 423 / 9,124 | §16.17(c): the 7 truncated workbooks are `not_ready`; COV diagnostics now attribute them (`not_ready_partial_marker: 7`; `delivered` 8,701, `not_ready` 423, `explicitly_excluded` 5,223 of 14,347 physical inputs) |
| `cov.fixtures` | PASS 0 / 44 | PASS 0 / 45 | the truncated-workbook fixture `cov-045` |
| RED `surfaces_scored` | four (logging listed but empty) | five, `surfaces_unexercised: []`, `logging` leaks 0 | §16.17(a)(b): the collector hears the runner-captured `app` records and every positive canary travels the degraded-parse carrier; leaks unchanged (structured payload 120, body 56, text 56; 120/120 canaries leak, 0/110 controls altered) |

Residual >400-line debt after the rounds (all under the 500 ceiling): `hidden_budget.py` 478, `gate_red.py` 472, `verify_step1.py` 469, `red_cases_b.py` 457, `snapshot.py` 451, `gate_vis.py` 456, `gate_cov.py` 413, `runner_logging.py` 405, `vis_matrix.py` 404. Whole-RED evaluation ~108 s per run (the carrier). This gate was superseded: the advisor's done-check refused the sleep-stitched run, and the re-run plus the Codex review of the round-6/7 code opened round 8 (next section); the instrument is complete after round 8, not here.

## Clean gate re-run, Codex round-6/7 review and round 8 (2026-09-08 07:46 → 10:17)

The advisor's done-check (2026-09-08 ~07:55) refused the stitched clean gate above (a sleep-straddled 705 plus a 5/5 re-run) and the review gap (rounds 6–7 changed seven files after Codex's last look). Both were re-done in parallel, ALONE.

**Clean gate re-run** (`tests/tools`, one uninterrupted process, 07:46–08:04, log `clean_gate_run_2.txt`): **707 passed / 1 failed** in 18:16, no probe left. The failure is real, not a sleep artefact: `test_census.py::test_take_census_is_deterministic_over_an_unchanged_org` saw every metric and the `corpus_digest` equal EXCEPT `schema_database_size_bytes` (`SELECT pg_database_size(current_database())::bigint`): 11 426 839 → 11 272 719 between two consecutive censuses ~0.2 s apart with no write between, on a freshly seeded probe under autovacuum (cluster `autovacuum=on`, naptime 1 min). `pg_database_size` measures files on disk outside MVCC; it had passed in every earlier run. Verdict: CONFIRMED — the metric cannot be deterministic by construction; the contract mandates the key (§16.5) but promises census determinism nowhere (the sealed test derived it). Ruled as §16.18(a): informational key, excluded from determinism and equality; no instrument change; the sealed test amended (amendment 13). Blast radius checked: the instrument only WRITES the census (`release.py:257`); nothing compares census metrics for equality; `corpus_digest` never included the size.

**Codex review of the round-6/7 code** (GPT-6 Astra, max, read-only, 07:49–08:04; brief `codex_brief_round67_code.md`, output `codex_review_round67_code.md`): `NOT SOUND` — A41, A42, A43, A45, B10, B11, B12 IMPLEMENTED; A44 and B9 with gaps. Orchestrator's verdicts against the source:

| # | Codex finding | Verdict + evidence | Row |
|---|---|---|---|
| 1 | `validation_guard.py:276` `_admitted_candidate` trusts `_load`'s last-wins admission map: `admit X(A) → abort X → founder_reset X(B) → admit X(B)` (a second `validation_admission` under the same attempt id, distinct envelope id) resolves X's candidate as B and admits B | CONFIRMED, major (latent, Stage C): `_load` builds `admissions` as a dict comprehension keyed by `attempt_id` (line 89) so the later event wins; `_require_covered_abort` then finds the abort covered by a reset whose pair equals the LAST admission's pair and the proposed pair equal too — exactly the swap A44/(j) was ruled to stop, reopened through one appended event. Attempt ids are `uuid4()` (`record_admission`), so only an appended event produces the duplicate — but the reset is an appended event as well, so the guard must hold against appended events. Ruled as §16.18(b): a duplicate admission per attempt id refuses, naming the id | **A47** |
| 2 | `hidden_budget.py:475` `_free_repeat` keeps the LAST matching RESERVATION with a completed outcome (reservation order): `reserve R1 → reserve R2 → complete R2 → complete R1` replays R2; a tampered R2 cache aborts although R1's newer cache is good | QUALIFIED, minor: the loop is over reservation events in ledger order, so "latest" is by reservation (lines 462–475) while (h) says "latest completed"; nothing serialises whole runner invocations — the package has a per-probe lease (`probe_db.py`, one probe per run) and a per-append file lock on the ledger (`hidden_budget.py`, `.lock` sibling via `msvcrt`/`fcntl`, so the limit cannot be overrun), and `reserve` does not refuse while a same-pair reservation is still open — so two concurrent runs of one pair CAN interleave, though both results of one pair are the same measurement, so the effect is which cache is read. Ruled as §16.18(c): completion order | **A48** |

Codex AGREED: timestamps confirm the seven-file scope; twelve focused probes passed (close idempotency, interruption cleanup, replay precedence, B12 diagnostics, argparse); no payload or traceback leaked; seal 82/82; scoped ruff clean; four-section docstrings, 28–478 lines. NOT VERIFIED by Codex: filesystem-backed round-7 seals, the RED battery, live PostgreSQL.

### Round 8 (A46 = amendment 13; A47–A48 sealing tests = amendment 14)

| Row | WHAT | Sealing test | Owner |
|---|---|---|---|
| A46 | census determinism excludes `schema_database_size_bytes` (§16.18(a)); the key stays in `CENSUS_V1`, `sql` fixed, positive integer; instrument unchanged | `test_census.py` amended (amendment 13) | oracle author |
| A47 | an `attempt_id` with more than one `validation_admission` on the lock makes `check_validation_preconditions` refuse, naming that attempt id only, before any candidate is resolved; the (j) sequences still behave as sealed; `lock_state` unchanged except that `foreign` (→ `revoked`) considers every admission event of a verdict-bearing attempt, not the last (§16.18(b)) | `test_review_round_8_guard.py` (the precondition) + `test_review_round_8_guard_b.py` (the derivation clause) | F8 |
| A48 | the free repeat loads the completed result whose deciding outcome appears LAST in the ledger (completion order); the superseded cache stays unread; the last outcome per reservation still decides its state (§16.18(c)) | `test_review_round_8_hidden_budget.py` | F8 |

### Codex cross-vendor review of the round-8 sealing tests (pass 1, 2026-09-08 ~08:32–08:40) — reconciled

Brief `codex_brief_round8_tests.md`, output `codex_review_round8_tests.md` (GPT-6 Astra, max, read-only): `NOT SOUND`, rulings (a), (b), (c) each with a gap — 4 MAJOR, 4 MINOR. Orchestrator's verdicts against the source and the rulings:

| # | Codex finding | Verdict + evidence | Action |
|---|---|---|---|
| 1 | `test_census.py:239` equality of the two `sql` values does not seal the FIXED literal; `sql="SELECT 1"` in both passes | CONFIRMED, minor — §16.18(a) names the literal | assert the literal `SELECT pg_database_size(current_database())::bigint` |
| 2 | `test_census.py:241` `isinstance(value, int)` accepts `True` | CONFIRMED, minor | exclude `bool` |
| 3 | `guard.py:165` every duplicate case authorizes the proposed candidate; a duplicate journal checked with an UNAUTHORIZED candidate yields the generic authorization refusal without the attempt id | REFUTED as a gap: §3.7 lists (a) authorization before (b) state, and `check_validation_preconditions` judges (a) first (appending `unauthorized_attempt`) — the generic refusal is the contract's order, not an escape; within the reachable `attempted` path every admission on the lock belongs to the one aborted attempt, so "duplicates checked only at candidate resolution" is behaviourally the same rule. QUALIFIED for the seal: the precedence was unwritten | §16.18(b) now states the precedence; a CONTROL pins it (unauthorized candidate + duplicate journal → the (a) refusal, `unauthorized_attempt` appended) |
| 4 | `guard.py:248` the only foreign case has the intruder FIRST; a first-admission-only projection passes and returns `consumed` for authorized → foreign → authorized | CONFIRMED, major — (b) says EVERY admission | add foreign-middle (red today) and foreign-last (green today) cases plus an all-authorized duplicate control expecting `consumed` |
| 5 | `guard.py:151` the R5 exclusion list omits the session and run ids; a refusal echoing `SESSION` passes | CONFIRMED, minor | forbid the session, both run ids and the intruder principal too |
| 6 | `guard.py:96` the duplicate is appended with `guard.ADMISSION_EVENT`, an implementation constant; a conforming rename fails the seal | CONFIRMED, minor — the sibling seal `test_validation_guard.py:163` uses the literal `"validation_admission"` (§3.7, §16.1) | use the literal |
| 7 | `hidden_budget.py:175` the in-order control withdraws R1 (`failed`) before R2 exists; an implementation selecting the OLDEST completed reservation passes all five cases | CONFIRMED, major — vacuity against a plausible wrong rule | add `reserve R1 → reserve R2 → complete R1 → complete R2` → R2, both completions retained (green today; kills the oldest-wins mutation) |
| 8 | `hidden_budget.py:142` a successful replay does not prove the superseded cache stayed UNREAD: reading R2's tampered bytes, ignoring them, then loading R1 passes | CONFIRMED, major — (c) says unread; the round-5 seal already owns an open recorder (`_recording_opens`, private to that module) | record file opens during the replay inline in the round-8 file: R2's cache never opened, R1's opened once |

Codex AGREED: the author's red/green split reproduces in memory (guard reds 158/172/242, budget reds 114/131), the guard mutations survive the existing validation and round-7 controls, all 22 cases collect, ruff clean, docstrings/AAA/unique names, synthetic fixtures, no database opened. NOT VERIFIED by Codex: filesystem-backed execution, the live census (the author's 10/10 log was not re-run — the orchestrator's full run covers it).

### Codex pass 3 over the recorder delta of `test_review_round_8_hidden_budget.py` (2026-09-08 ~08:50–08:58) — reconciled

While pass 2 ran, the author widened the inline open recorder (`Path.open` + `io.open` + `builtins.open`, reentrancy-counted) — 264 → 278 lines, `39c057ed…` → `2f3ed233…`. Pass 3 (brief `codex_brief_round8_tests_pass3.md`, output `codex_review_round8_tests_pass3.md`) reviewed that file alone: `NOT SOUND`, 2 MAJOR, 2 MINOR.

| # | Codex finding | Verdict + evidence | Action |
|---|---|---|---|
| 1 | `:155` reads through `os.open`/`os.read` or `io.FileIO` bypass the recorder; a mutation that reads R2 that way and then loads R1 passes | QUALIFIED, minor: the seal targets the plausible wrong fix (read-and-verify, or read-and-ignore, through the instrument's own `Path.read_bytes` route), not an implementation dodging the recorder on purpose; `os.open` is one more line and closes the only realistic low-level route; `io.FileIO` is a C type the instrument never names — out of proportion | superseded by pass 2 #1: the recorder becomes an interpreter audit hook (`sys.addaudithook`, event `open`, the round-5 seal's own pattern), which sees `os.open` and `io.FileIO` as well |
| 2 | `:150` `str(file)` misnames byte filenames: `open(os.fsencode(path), "rb")` on R2 escapes and on R1 falsely fails | CONFIRMED, minor (recorder correctness) | normalise with `Path(os.fsdecode(file))` |
| 3 | `:195` "R1 opened exactly once" is stronger than §16.18(c) ("exactly one cache is read"); a loader reading R1 in two opens would fail while conforming | QUALIFIED — the open COUNT is an implementation detail; the ruled property is WHICH caches are read | assert R2 opened 0 times, R1 at least once, no other file under `hidden_budget.results/` opened |
| 4 | `:183` the new tests lack per-function docstrings (A4) | REFUTED by the suite's convention: the sealed oracle's 700+ tests carry the contract in the module docstring and the `test_<what>_<condition>_<expected>` name; ruff (the gate) enforces no per-test docstring; churning one file would make it the odd one out | none |

Codex AGREED: hash matches; on the project's Python 3.12.12 `Path.read_bytes`/`read_text` go through `Path.open` and count once; restoration after success and exceptions works; ledger reads do not inflate cache counts; the completion-order reference implementation passes the scoped cases; 278 lines, four-section docstring, AAA, ruff clean, seven tests collect.

### Codex pass 2 over the four round-8 files (2026-09-08 ~08:48–09:00) — reconciled

Brief `codex_brief_round8_tests_pass2.md`, output `codex_review_round8_tests_pass2.md`: `NOT SOUND` — pass-1 items 1–7 CLOSED, item 8 OPEN; 2 MAJOR, 1 MINOR. Codex reproduced the author's 7 red / 11 green split with unchanged test bodies and confirmed the amendments kill the first-admission, oldest-reservation and `Path.read_bytes`-snooping mutations.

| # | Codex finding | Verdict + evidence | Action |
|---|---|---|---|
| 1 | `hidden_budget.py:155` the name-patching recorder misses `os.open`/`os.read`; a mutation reading R2 through descriptors and then loading R1 passes; Codex suggests interpreter `open` audit events as the round-5 seal does | CONFIRMED (same route as pass 3 #1, stronger fix): `sys.addaudithook` fires for `builtins.open`, `io.open`, `os.open` and `io.FileIO` alike, so one recorder covers every route; hooks cannot be removed, so the round-5 pattern (installed once, records only while a list is attached) applies | inline audit-hook recorder; assert R2 0 opens, R1 ≥ 1, no other file under `hidden_budget.results/` |
| 2 | `guard_b.py:148` the all-authorized duplicate control uses ONE principal twice; "revoke whenever an attempt's admissions disagree on principal" passes all 28 guard cases yet wrongly revokes two DIFFERENT authorized principals under X | CONFIRMED, major (vacuity against a plausible over-broad fix) | add the control: two authorizations with two principals, X admitted by both, verdict → `consumed` |
| 3 | `guard.py:211` the precedence control never asserts the attempt id is ABSENT from the (a) refusal; an implementation that appends `unauthorized_attempt` and then raises the duplicate message naming X passes | CONFIRMED, minor — §16.18(b) says the (a) refusal carries no attempt id | assert X not in the message |

Codex AGREED: all 28 cases collect; ruff clean; 245/250/150/278 lines; the census diff is confined to the one function and rejects wrong SQL and booleans; the widened R5 exclusions reject session, run ids and the intruder; fixtures synthetic; no database opened. NOT VERIFIED: filesystem execution, the live census (covered by the orchestrator's full run).

### Codex pass 4 (closing, 2026-09-08 ~09:12–09:23) and the round-8 seal

Brief `codex_brief_round8_tests_pass4.md`, output `codex_review_round8_tests_pass4.md`: **`SOUND`** — items A (audit-hook recorder: real audit events cover all four APIs and str/bytes/PathLike paths, descriptors skipped, installed once, detaches after success or exceptions), B (selected-cache opens may repeat; superseded and other cache opens rejected), C (two distinct authorized principals → `consumed`), D (authorization-first control requires the attempt id's absence and one more `unauthorized_attempt`) all CLOSED; no new defect; the 7 red / 12 green split reproduces; 29 cases collect; ruff clean; 245/251/169/293 lines. The test-review loop closed after four passes (8 → 3 + 4 → 0 findings). **Amendment 13** (`test_census.py` `664fe6f4…` → `de99da5c…`, reverse-hash proof against the orchestrator's pre-amendment copy, 11 changed lines in the one function) and **amendment 14** (`test_review_round_8_guard.py` `f67a07ef…`, `test_review_round_8_guard_b.py` `aed127d6…`, `test_review_round_8_hidden_budget.py` `7efe6c49…`) recorded at ~09:25; the seal is **85 lines, 85/85 OK**. Fixer F8 launched against it.

### Round 8 result (fixer F8, 2026-09-08 ~09:27–09:53)

Rows A47 and A48 implemented in `validation_guard.py` (377 → 440 lines), `hidden_budget.py` (478 → 457) and the pre-authorized new leaf `hidden_budget_replay.py` (106). Fixer's own gates: seal 85/85 before and after; the three round-8 pure seals 19 passed (from 7 red / 12 green); `test_validation_guard*.py` + `test_review_round_7_pure.py` 17 passed; the hidden-budget and round-5 pure families 31 passed; `test_census.py` alone 10 passed; the seven `test_verify_step1*.py` modules ALONE 36 passed (970 s); the five round-5/7 runner modules 22 passed; ruff + format clean (109 files); size gate exit 0; no probe left; no disagreement with the seals or the contract. Orchestrator's own re-run of the round-8 seals plus the guard and budget families: **67 passed**; seal, ruff and size gate re-verified. Shape: `_load` groups this lock's admissions per attempt id in ledger order (`_Journal.admissions: dict[str, tuple[dict, ...]]`, a `duplicate_admissions` property); `_refuse_duplicate_admissions(journal)` sits in `check_validation_preconditions` after the authorization check and the state check, immediately before `_require_covered_abort` — message `more than one validation_admission is recorded under attempt <id>[, <id>…] on this lock; the journal is malformed, refusing to read the holdout` (ids only, R5); `_derive`'s foreign test iterates every admission of every verdict-bearing attempt; `_admitted_candidate` reads the last admission, which is the only one once the duplicate refusal has passed; `record_admission` unchanged. `select_replayable_outcome` (leaf) scans the whole ledger first, maps each reservation id to its LAST outcome event and that event's position, and among the pair's reservations whose deciding outcome is `completed` picks the greatest deciding position — completion order; `_load_result` then opens the selected digest alone; a deciding `completed` outcome without a digest still raises for every matching reservation. Deviation, recorded: `RESERVATION_EVENT` and `OUTCOME_EVENT` moved to the leaf (the leaf projects those kinds; importing them back would cycle) and are re-exported from `hidden_budget` together with `BUDGET_RAISE_EVENT`/`OUTCOME_VALUES`, which stay — every name the sealed tests import still resolves. Unpinned, carried forward: no sealed test covers the "completed outcome carries no result digest" corruption raise (retained as before).

### Codex confirm-closed review of the round-8 code (2026-09-08 ~09:55–10:05)

Brief `codex_brief_round8_code.md`, output `codex_review_round8_code.md` (GPT-6 Astra, max, read-only; concurrent with the final gate, no database access): **`SOUND`** — A47 CLOSED (the duplicate refusal precedes aborted-candidate resolution; lock scoping, refusal precedence and every-admission revocation as ruled), A48 CLOSED (completion order selects the right reservation and one cache; withdrawals, corruption checks and exhaustion intact); zero findings; 61 scoped test bodies, 14 adversarial probes and 2 output probes passed in memory; refusals preserve R5/R11; public imports resolve without a cycle; seal 85/85; ruff and house checks pass; three-file scope confirmed by timestamps. Recorded input-side: the brief file named two of the three round-8 seals (the task message and the sealed tree carried all three, and the review's every-admission finding covers `guard_b`). NOT VERIFIED by Codex: Python 3.12 execution (it ran 3.14), filesystem durability, the full oracle — covered by the final gate below.

## Final gate after round 8 (§14, 2026-09-08 09:55 → 10:17, orchestrator's own runbook `final_gate_runbook_r8.sh`, ALONE, one uninterrupted process) and the baseline after round 8

Static half: `ruff check` + `ruff format --check` clean (194 files); size gate exit 0; seal **85/85** OK; all **108** instrument modules import in a fresh interpreter (107 + the round-8 leaf). The full sealed suite (`tests/tools`, one process, no sleep, no re-run): **727 passed / 0 failed** in 18:43, exit 0; no probe before, none after. Draft re-cut: lock `e5e05b91…` (was `014a9d9e…`), 75 visible files verified, runner `d57d72ba…` (was `b1104d53…`; `validation_guard.py`, `hidden_budget.py`, the new `hidden_budget_replay.py`), fixtures digest unchanged. Baseline `after-round-8`, run `20260908t071408z_624135d9`: `STEP1 TUNING: 1/17 PASS | provisional=4:FID,THR,IDENT,ATTR | directional=- | run_id=20260908t071408z_624135d9 | lock=sha256:e5e05b91… | runner=sha256:d57d72ba…`, exit 2 (ERROR: 5 FAIL, 11 incomplete, SNAP PASS). Compared with the after-round-7 run `20260908t043633z_7bc924c2`: every gate status identical, all 58 criteria entries identical, `corpus_digest`, `text_digest` and `code_hash` identical — only `runner_sha256` moved, as it must. Corpus untouched (0023; 5,893 / 8,454 / 839 / 5,893). The baseline that the committed code reproduces is THIS one; the after-round-7 line above is superseded.

Stage A instrument: complete after round 8 in substance — but see the two sections below: the bytes were then normalized to LF (amendment 15) and the gate re-run, so the LF section is the one the commit reproduces. Sealed oracle 85 files / 727 tests (amendments 1–14 at this point); instrument 133 files / 108 modules; residual >400-line debt: `gate_red.py` 472, `verify_step1.py` 469, `red_cases_b.py` 457, `hidden_budget.py` 457, `gate_vis.py` 456, `snapshot.py` 451, `validation_guard.py` 440, `gate_cov.py` 413, `runner_logging.py` 405, `vis_matrix.py` 404. Carry-forwards for Stage B: the §16.16(o) list, C1 root-logger capture, observer-on-replay (Stage C), the digest-less completed outcome (unpinned), whole-RED ~108 s per run.

## Line-ending normalization before the commit (amendment 15, 2026-09-08 ~10:35) and the final gate on the LF bytes

The advisor's done-check asked for one more measurement before the commit request: whether the byte seal survives a fresh checkout. It would not have. The repository's `.gitattributes` declares `*.py text eol=lf` and this machine runs `core.autocrlf=true`, so git stores LF and an `eol=lf` checkout restores LF — while 121 of the 218 files under `backend/tools` and `backend/tests/tools` (48 of the 85 sealed files) had been written with CRLF by the agents' tools (`git hash-object` of a CRLF sealed file already equalled the LF blob). The seal, computed over the working-tree bytes, would therefore fail on any other checkout, and `runner_sha256` (the Merkle digest over `backend/tools/mem01_verify/`, json/yaml/md included) would differ per platform. Decision (orchestrator, under the repository's own declared convention; recorded for the founder, who can still choose the alternative before committing): normalize every file in the two trees to LF — no other byte changed, no binaries present, no lone `
` anywhere — and pin both trees with `backend/tools/** text eol=lf` and `backend/tests/tools/** text eol=lf` in `.gitattributes` (committed with the docs group), so seal and runner hash are identical on every platform. The alternative was `-text` (store CRLF verbatim), rejected because it would carve a mixed-ending exception into a repository that declares LF. Amendment 15 records the re-seal with a reverse proof for all 48 changed sealed files (re-inserting `
` for `
` reproduces each old digest); the 37 sealed files that were already LF keep their lines. Because the sealed bytes moved, the whole final gate was run again on the LF trees (next section) — the run that the committed bytes reproduce.

## Final gate after round 8 on the LF bytes (§14, 2026-09-08 10:28 → 10:51, `final_gate_runbook_lf.sh`, ALONE, one uninterrupted process) — THE gate the committed bytes reproduce

Two launches, recorded as they happened. The first (10:23) was a session background task; the session's memory watchdog killed it about a minute into the suite, and the runbook shell carried on into the re-cut and a stray baseline (`20260908t072750z_df3adc62`) — that baseline was stopped by hand at 10:30, its probe dropped, its partial report directory removed; the suite process of that launch was killed with its tree and its two probes dropped. The second launch ran as a detached OS process outside the watchdog. Its "probes before" line still lists the stray probe (dropped ~90 s later, while the suite was already running); nothing else overlapped.

Static half: ruff + format clean (194 files); size gate exit 0; seal **85/85** OK on the LF bytes; **108** modules import. Full sealed suite: **727 passed / 0 failed** in 18:24, exit 0; no probe after. Draft re-cut: lock `34fc4f67…`, 75 visible files, runner `716696120a…` (the LF Merkle digest), fixtures digest unchanged. Baseline `after-lf-normalization`, run `20260908t074745z_c1350482`: `STEP1 TUNING: 1/17 PASS | provisional=4:FID,THR,IDENT,ATTR | directional=- | run_id=20260908t074745z_c1350482 | lock=sha256:34fc4f67… | runner=sha256:716696120a…`, exit 2 (ERROR: 5 FAIL, 11 incomplete). Against the after-round-8 run `20260908t071408z_624135d9`: every gate status, all 58 criteria entries, `corpus_digest`, `text_digest` and `code_hash` identical; only `runner_sha256` moved (the bytes changed only in line endings). Corpus untouched (0023; 5,893 / 8,454 / 839 / 5,893). This run supersedes the after-round-8 line above as the baseline the committed code reproduces; the after-round-8 hashes remain a valid record of the CRLF working tree that never reached git.

Stage A instrument: **COMPLETE.** Sealed oracle 85 files / 727 tests (amendments 1–15, all with reverse proofs); instrument 133 files / 108 modules, all LF, pinned by `.gitattributes`. Commit request issued to the founder.
