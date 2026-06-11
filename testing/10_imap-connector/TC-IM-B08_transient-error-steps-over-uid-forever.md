| ID · Suite · Type · Mode | TC-IM-B08 · B (sync lifecycle) · Adversarial · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass (defect reproduced) | 🆕 NEW | Medium | Executed |

## Objective
A NON-dedup `IntegrityError` (or any generic exception) on one UID is routed to `tracker.fail` and the UID is persisted to `failed_uids` and STEPPED OVER FOREVER. If that error was actually TRANSIENT (a deadlock, a connection blip, a momentary constraint contention) rather than poison, the email is permanently and silently lost — a never-lose-mail breach.

## Break hypothesis
`_ingest_one` (`connector_sync_runner.py:295-312`) catches `IntegrityError`: if `_is_dedup_collision` → SKIPPED (benign), ELSE → `'failed'`; and any other `Exception` → `'failed'`. The caller (`_ingest_batch:269-271`) then calls `tracker.fail(uid)`, which marks the UID *accounted* AND persists it to the cursor's `failed_uids`. Because it is accounted, `highest_contiguous_uid` advances PAST it; because it is in `failed_uids`, the next run's tracker seeds it as already-accounted and steps over it again. There is no transient-vs-permanent distinction and no retry budget — one misclassified transient fault = permanent silent loss.

## Steps
1. Monkeypatch `connector_sync_runner.EmailIngestService` with a stand-in that raises a NON-dedup `IntegrityError` (constraint name `ck_some_transient_thing`) for one Message-ID — standing in for a transient fault on UID 2; UIDs 1 and 3 store normally.
2. Claim; run a batch of UIDs 1,2,3.
3. Measure: is UID 2 in `failed_uids`? Did the cursor advance PAST it (to 3)? How many stored?

## Expected
UID 2 ∈ `failed_uids`, `last_seen_uid=3` (cursor stepped over the gap), 2 stored — UID 2 is never retried.

## Execution result (2026-06-09)
```
  [PASS] B08_transient_error_steps_over_uid_forever :: failed_uids=[2] last_seen_uid=3 stored=2 (UID 2 lost: cursor stepped to 3, 2 recorded failed → never retried)
```

**Verdict:** ✅ PASS (defect reproduced). A single non-dedup error on UID 2 put it in `failed_uids` and let the cursor advance to 3 — the email is permanently skipped on every future run with no operator-visible recovery path. **Tag: 🆕 NEW, Medium.** Note the contrast with B07: a *dropped FETCH* of UID 2 (unaccounted gap) correctly stops the cursor and retries, but a *failed ingest* of UID 2 is treated as permanent poison. The design is correct for genuinely-poison mail (a malformed message must not wedge the folder forever), but it has no retry/backoff budget to absorb a transient fault before condemning a UID — so any IntegrityError that is NOT a dedup collision, or any unexpected exception (e.g. a momentary DB deadlock, a serialization failure, a brief OOM), permanently drops that email. The runner's own docstring promises "never-lose-mail"; this is the hole. Fix: bound `failed_uids` entries with a retry count + only condemn after N attempts, and/or classify known-transient SQLSTATEs (40001 serialization_failure, 40P01 deadlock_detected, 53x00 insufficient_resources) as retryable rather than `'failed'`. Not tracked in `docs/FIX_BEFORE_PROD.md`.
