| ID · Suite · Type · Mode | TC-IM-B07 · B (sync lifecycle) · Positive · runner |
|---|---|

| Result | Tag | Severity | Status |
|---|---|---|---|
| ✅ Pass | — (positive/contract) | Info | Executed |

## Objective
A requested-but-unreturned UID (a dropped/partial FETCH of a still-existing message) must STOP the cursor at the gap — the mail is not lost and the cursor does not skip past it. (Reconfirm of `test_run_does_not_advance_past_a_requested_but_unreturned_uid`.)

## Break hypothesis
If the runner advanced over `requested_uids` it never received back, a message the server failed to return in one cycle would be skipped forever (silent loss). The defence: `highest_contiguous_uid` (`fetch_planner.py:45-59`) stops at the first UID that is requested-but-not-accounted; a returned-but-missing UID is an unaccounted gap.

## Steps (prove once)
1. Claim; run a batch that REQUESTED [1,2,3] but RETURNED only 1 and 3 (UID 2 dropped).
2. Assert the cursor stopped at `last_seen_uid=1` (not 3) and 2 emails stored (1 and 3); UID 2 is retried next run.

## Expected
`last_seen_uid=1`, 2 stored.

## Execution result (2026-06-09)
```
  [PASS] B07_gap_stops_cursor_no_skip :: last_seen_uid=1 (must be 1, not 3) stored=2 (UID 2 retried next run)
```

**Verdict:** ✅ PASS. The contiguous-prefix advance stopped at the gap (UID 2), keeping the cursor at 1 so the dropped UID is re-fetched — never-lose-mail held for a dropped FETCH. **Tag: — (positive/contract).** Reconfirmed live.

> **Contrast with TC-IM-B08:** a *dropped* UID (this case) stops the cursor and is retried; a UID routed to `tracker.fail` (B08) is *accounted* and stepped over forever. The never-lose-mail guarantee depends entirely on which path an error takes — see B08.
