# Codex Review: IMAP Incremental Fetch Findings

Date: 2026-06-07

## Scope

This report covers the latest review findings for the IMAP incremental fetch path.

Reviewed files:

- `backend/app/connectors/imap/fetch_session.py`
- `backend/app/connectors/imap/sync/fetch_planner.py`

## Executive Summary

The review identified two P2 issues in the new IMAP incremental fetch implementation:

- A connected IMAP socket can be leaked when login fails after the TCP connection succeeds.
- Unknown message sizes are treated as zero bytes during batch planning, which can collapse many messages into a single oversized fetch.

Both issues affect operational reliability. The first can exhaust IMAP connections during repeated authentication failures. The second can violate the byte-bounded batching invariant and produce large `BODY.PEEK[]` requests.

## Finding 1: IMAP Socket Is Not Closed When Login Fails

Severity: P2

File:

- `backend/app/connectors/imap/fetch_session.py`

Issue:

`open_imap_session()` creates a real `imaplib` client inside `_connect_login()`. If the TCP connection succeeds but `client.login()` fails, the function raises `ImapAuthError` or `ImapConnectionError` before returning a `DefaultImapFetchSession`.

Because no session object is returned, the normal `close()` path is never available to the caller. The outer exception handler shuts down the executor, but it does not log out or close the already connected IMAP client.

This affects at least these paths:

- Invalid credentials.
- Expired app password.
- Server-side login rejection.
- Socket or protocol error during login after the connection has been opened.

Impact:

- Repeated sync attempts with bad credentials can leak IMAP connections.
- Leaked sockets may remain open until garbage collection or OS cleanup.
- Providers may throttle or reject future connections if connection limits are reached.
- The sync worker can look healthy while leaving remote resources open.

Recommended fix:

- Track whether a client was created before calling `login()`.
- On login failure or login-time connection errors, attempt to close the client before raising the sanitized domain error.
- Prefer a small helper that tries `logout()` first, then falls back to a lower-level socket close if available.
- Do not let cleanup failures mask the original authentication or connection error.

Suggested implementation shape:

```python
client: imaplib.IMAP4 | None = None
try:
    client = connect_client()
    client.login(params.username, secret)
    return client
except imaplib.IMAP4.error as exc:
    if client is not None:
        safe_close_client(client)
    raise ImapAuthError("IMAP authentication failed.") from exc
except OSError as exc:
    if client is not None:
        safe_close_client(client)
    raise ImapConnectionError("IMAP connection error during login.") from exc
```

Suggested test coverage:

- Simulate successful TCP connection followed by `login()` raising `imaplib.IMAP4.error`.
- Assert the client is logged out or otherwise closed.
- Assert the executor is shut down.
- Assert the public error remains `ImapAuthError` and does not include the secret.
- Simulate an `OSError` during login after connection and assert the same cleanup behavior.

Acceptance criteria:

- No connected IMAP client is left open when login fails.
- Cleanup failures during login-error handling do not hide the original sanitized exception.
- Existing successful login/session behavior is unchanged.

## Finding 2: Unknown Message Sizes Collapse Byte-Bounded Batches

Severity: P2

File:

- `backend/app/connectors/imap/sync/fetch_planner.py`

Issue:

`batch_by_bytes()` currently reads each message size with `sizes.get(uid, 0)`. If `fetch_sizes()` cannot return sizes for one or more UIDs, those messages are treated as zero bytes.

That means a batch containing many unknown-size messages can be planned as if it has no byte cost. The result can be a single large `BODY.PEEK[]` fetch containing many messages whose actual combined size exceeds the intended byte limit.

This violates the stated batching invariant that fetch batches should stay near the configured byte target while still allowing at least one UID per batch.

Impact:

- Large or unbounded fetch requests can be sent to the IMAP server.
- Sync can become slow, memory-heavy, or provider-throttled.
- A missing size response can turn a conservative fetch plan into the riskiest possible batch.
- The byte-bounded fetch invariant is not reliable under partial `RFC822.SIZE` responses.

Recommended fix:

Handle missing sizes conservatively. Two reasonable approaches:

1. Put unknown-size UIDs into single-message batches.
   - This is the safest behavior.
   - It preserves byte-bounded planning for all known-size messages.
   - It avoids guessing about the size of messages the server did not report.

2. Use a nonzero fallback size.
   - For example, use `batch_bytes` so each unknown-size message naturally becomes its own batch.
   - Or use a smaller configured fallback if the product wants to trade safety for throughput.

The one-UID-per-batch behavior is preferable unless there is a measured reason to batch unknown-size messages together.

Suggested test coverage:

- Unknown-size UIDs are not grouped together as zero-byte messages.
- Known-size UIDs continue to batch according to `batch_bytes`.
- Mixed known and unknown sizes preserve UID order and do not create oversized groups around unknown messages.
- A single known message larger than `batch_bytes` still forms a one-message batch, preserving the existing invariant.

Acceptance criteria:

- Missing `RFC822.SIZE` entries do not cause multiple unknown-size messages to collapse into one large batch.
- The planner remains deterministic and preserves UID ordering.
- Batch output always contains at least one UID per batch.

## Overall Recommendation

Fix both findings before treating the IMAP incremental fetch path as production-ready.

The login cleanup issue should be fixed in the session boundary because it owns the connected `imaplib` client. The batching issue should be fixed in the pure planner so the invariant is enforced independently of the IMAP provider behavior.

## Verification Checklist

- Login failure after connection closes or logs out the IMAP client.
- Executor shutdown still happens after connection or login failures.
- Authentication failures still raise `ImapAuthError` without leaking secrets.
- Login-time socket errors still raise `ImapConnectionError`.
- Unknown-size UIDs are batched conservatively.
- Known-size batching behavior remains unchanged.
- Backend tests pass with `docker compose exec backend uv run pytest`.

---

## Resolution + Adversarial Re-Review (2026-06-09)

Both Codex findings were verified FIXED against the live source, then the fetch path was put
through a multi-agent adversarial sweep (confirm fixes → blind 4-lens sweep → refute-by-default
verification, 54 agents) to find what a single-vendor pass missed. Net result below; full backend
suite **429 passed, 90.89% coverage**.

### Codex findings — CONFIRMED FIXED

- **Finding 1 (socket leak on login fail):** `fetch_session.py:_connect_login` closes the connected
  client via `_safe_close` on BOTH `imaplib.IMAP4.error` and `OSError` before raising a secret-free
  error; the executor is torn down in the outer `except BaseException`. Exhaustiveness verified:
  `login()` can only raise those two types. Tests: `test_open_imap_session_closes_client_on_auth_failure`,
  `…_on_login_oserror`, `…_propagates_a_connect_failure`.
- **Finding 2 (unknown-size batch collapse):** `fetch_planner.batch_by_bytes` charges an unknown-size
  UID the full `batch_bytes`, so consecutive unknowns each split. Tests:
  `test_batch_by_bytes_unknown_size_becomes_its_own_batch`, `…_consecutive_unknowns_each_split`.

### New issues the sweep found — and their fixes

- **[HIGH · data-loss] Dropped-UID silent mail loss.** The cursor advanced over a UID that `SEARCH`
  found + `FETCH` requested but `fetch_messages` did not return (a partial `OK`, a parse-filter drop
  of a still-existing message, or an interior expunge), because `highest_contiguous_uid` was fed
  only the RETURNED UIDs — its gap-stop was dead code. **Fixed:** `FetchBatch` now carries
  `requested_uids`; the runner advances over the requested set and treats a requested-but-unreturned
  UID as an unaccounted gap that STOPS the cursor → re-fetched next run. Trade-off: a *permanently*
  unfetchable-but-searchable UID now wedges that folder (visible, non-destructive) instead of losing
  mail silently — the correct direction. Regression test:
  `test_run_does_not_advance_past_a_requested_but_unreturned_uid`.
- **[MEDIUM · throughput] `_SIZE_RE` required `UID` before `RFC822.SIZE`.** Order-flipping servers
  yielded empty sizes → one FETCH per message. **Fixed:** UID and size matched independently
  (`_SIZE_VALUE_RE`). Test: `test_fetch_sizes_parses_either_data_item_order`.
- **[LOW · lifecycle] Held IMAP session not deterministically closed** on early-return / exception
  out of the fetch loop, and `close()` could skip executor shutdown if the logout await was
  cancelled. **Fixed:** runner wraps the stream in `contextlib.aclosing`; `close()` shuts the
  executor in a `finally`.
- **[LOW · latent] INTERNALDATE `%b` was locale-dependent** (DACH `de_DE` → wrong `received_at`).
  **Fixed:** parsed by component with a fixed English-month map (no `strptime %b`). Tests:
  `test_parse_internaldate_is_locale_independent`, `…_returns_none_on_garbage`.
- **[LOW · latent] `decode_mutf7` raised on malformed input** (display-only helper). **Fixed:**
  degrades to the raw escape, never raises. Test: `test_decode_mutf7_never_raises_on_malformed_input`.
- **[LOW · security] `imaplib._MAXLINE` process-global mutation.** The bump is genuinely required
  (a large mailbox's single-line `UID SEARCH min:*` reply) and per-instance scoping is impossible
  (imaplib reads the module global). Kept; the misleading comment was corrected.

### Noted, NOT fixed (low severity, regression risk)

- `open_imap_session` can leak an authenticated socket if its executor await is cancelled AFTER the
  worker logged in. Trigger is loop/process shutdown (OS reclaims the socket on exit) or an explicit
  per-sync cancellation that the current code never issues. A correct fix risks blocking the event
  loop on a logout round-trip; deferred.
