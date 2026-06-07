# Codex Review: Connector and IMAP Ingestion Findings

Date: 2026-06-07

## Scope

This review covers the connector creation flow, the IMAP fetch spike, and email date parsing behavior related to connector and email ingestion functionality.

Reviewed areas:

- `backend/app/connectors/services/connector_service.py`
- `spikes/imap_fetch.py`
- `backend/app/connectors/imap/parsing/email_parser.py`

## Executive Summary

The review identified three P2 issues that should be addressed before relying on this connector ingestion path in production-like workflows:

- Connector creation can return an unhandled 500 during a normal duplicate insert race.
- The IMAP spike can advance the incremental cursor beyond messages that were not fetched or written.
- Email date parsing can return naive datetimes even though downstream storage expects timezone-aware values.

Each issue is recoverable with a focused code change and targeted tests.

## Finding 1: Duplicate Connector Insert Race Can Surface 500

Severity: P2

File:

- `backend/app/connectors/services/connector_service.py`

Issue:

The connector creation path appears to perform an application-level duplicate check before inserting a connector. This pre-check is useful for returning a clean duplicate response in the common case, but it is not enough under concurrency.

If two admins create the same mailbox connector at nearly the same time, both requests can pass the `exists()` pre-check. One insert succeeds, and the other insert loses on the database uniqueness constraint, likely `uq_connector_connection_identity`.

The losing request can raise an uncaught database `IntegrityError`. If it is not translated into the expected domain error, the API can return a 500 instead of the documented duplicate response.

Impact:

- A normal duplicate create race can be reported as an internal server error.
- API behavior becomes inconsistent: duplicate requests may sometimes return 409 and sometimes 500.
- Operational logs may show avoidable error noise for a known and expected conflict condition.
- Clients cannot reliably handle duplicate connector creation.

Recommended fix:

- Keep the existing duplicate pre-check for fast feedback.
- Wrap the database insert or flush/commit path in an `IntegrityError` handler.
- Translate the uniqueness violation into `DuplicateConnectionError`, matching the service's documented duplicate behavior.
- Follow the same pattern used by `UserService.create_user` if that service already converts duplicate insert races into a domain-level conflict.

Suggested test coverage:

- Unit or integration test for duplicate connector creation when the repository pre-check returns false but the insert raises `IntegrityError`.
- Assert that the service raises `DuplicateConnectionError`.
- Route-level test, if applicable, asserting the API returns HTTP 409 for this race path.

Acceptance criteria:

- Duplicate connector creation always returns the documented duplicate conflict response.
- No uncaught `IntegrityError` escapes from this service path for the connector identity uniqueness constraint.

## Finding 2: IMAP Cursor Can Advance Past Unwritten Messages

Severity: P2

File:

- `spikes/imap_fetch.py`

Issue:

The IMAP spike advances `last_seen_uid` to the final UID in the requested batch. This is unsafe if the IMAP `FETCH` response is partial, empty, or otherwise missing messages from the requested batch.

For example, if the code requests UIDs `[101, 102, 103, 104]` but only successfully fetches and writes UIDs `[101, 102]`, advancing `last_seen_uid` to `104` causes the next incremental run to start after `104`. UIDs `103` and `104` may never be retried or written.

Impact:

- Email messages can be permanently skipped after a transient IMAP failure.
- Local output can falsely appear complete because the cursor says all requested UIDs were processed.
- Re-running incremental fetch may not repair the gap unless the cursor is manually reset.

Recommended fix:

Choose one of these behaviors and make it explicit:

1. Advance only through successfully fetched and written UIDs.
   - Track the UIDs that were actually persisted.
   - Advance `last_seen_uid` only to the highest contiguous successfully written UID.
   - Stop before any gap so the next run retries missing UIDs.

2. Treat a partial or empty fetch response as a retryable error.
   - Do not advance `last_seen_uid` when the response is incomplete.
   - Log or raise an error with the missing UID range.
   - Retry the batch on the next run.

The first approach is more tolerant of partial success. The second approach is simpler and safer for a spike if partial batch semantics are not needed yet.

Suggested test coverage:

- Simulate a batch where only a subset of requested UIDs is fetched.
- Assert the cursor does not advance past the last successfully written contiguous UID.
- Simulate an empty fetch result and assert the cursor remains unchanged.
- Include a case where all requested UIDs are fetched and written, and assert the cursor advances normally.

Acceptance criteria:

- No incremental run skips UIDs that were not successfully written.
- Cursor advancement reflects durable local processing, not only requested batch boundaries.

## Finding 3: Email Date Parser Can Return Naive Datetimes

Severity: P2

File:

- `backend/app/connectors/imap/parsing/email_parser.py`

Issue:

The email parser contract and ORM columns expect timezone-aware datetimes. However, common email date cases can still produce naive `datetime` values from `parsedate_to_datetime()`.

Examples include:

- Date headers with no timezone.
- Date headers using `-0000`.
- Received headers with unknown or ambiguous timezone abbreviations.

If a naive `datetime` is returned, downstream ingestion can mix naive and aware values or persist timestamps that are interpreted in the local process timezone rather than a deliberate canonical timezone.

Impact:

- Stored message timestamps may be inconsistent.
- Comparisons between naive and aware datetimes can fail at runtime.
- Ingestion behavior may vary by server locale or runtime timezone.
- Sorting, deduplication, retention, and audit behavior can become unreliable.

Recommended fix:

- Normalize every parsed email datetime before returning it.
- If `parsedate_to_datetime()` returns a naive value, attach a chosen canonical timezone, preferably UTC.
- Preserve existing timezone-aware values by converting or returning them consistently according to the parser contract.
- Document the fallback behavior for timezone-less or unknown-zone email dates.

Suggested implementation shape:

```python
from datetime import UTC

parsed = parsedate_to_datetime(value)
if parsed.tzinfo is None or parsed.utcoffset() is None:
    parsed = parsed.replace(tzinfo=UTC)
return parsed
```

Suggested test coverage:

- Date with explicit numeric timezone returns an aware datetime.
- Date with no timezone returns an aware UTC datetime.
- Date with `-0000` returns an aware UTC datetime.
- Malformed or unsupported dates continue to follow the existing fallback/error behavior.

Acceptance criteria:

- Parser return values always satisfy the timezone-aware datetime contract.
- ORM ingestion paths do not receive naive datetimes from email header parsing.

## Overall Recommendation

Address all three findings before merging or promoting this functionality. The connector duplicate race and parser normalization issues belong in backend service/parser tests. The IMAP cursor behavior should be covered by focused spike tests or converted into production ingestion tests if this spike code is moving into the main connector pipeline.

## Verification Checklist

- Connector duplicate insert race maps to `DuplicateConnectionError`.
- Connector API duplicate race returns HTTP 409, if exposed through a route.
- IMAP cursor does not advance past missing or unwritten UIDs.
- IMAP cursor advances normally when all requested UIDs are written.
- Email parser returns timezone-aware datetimes for explicit, missing, and unknown timezone inputs.
- Relevant backend tests pass with `docker compose exec backend uv run pytest`.
