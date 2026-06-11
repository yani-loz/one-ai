# TC-IM-C05 — No size limit anywhere: oversized email/attachment fully materialized + hashed

| ID · Suite · Type · Mode |
|---|
| TC-IM-C05 · C (Parse & data quality) · Boundary · pure |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 NEW · Low · Executed |

## Objective
Demonstrate that there is no size cap in the parse path — an oversized body and attachment are fully
materialized into memory and sha256-hashed without any limit.

## Break hypothesis
Neither `parse_email`, `_extract_body_text`, `_decode_text_part`, nor `_attachment_bytes`/
`_extract_attachments` checks any size threshold. `part.get_content()` / `get_payload(decode=True)`
fully decode into memory; `sha256(payload)` walks the whole buffer. A large message therefore
materializes in full (×decoded-copies) with no rejection — a resource-exhaustion lever.

## Steps
Parse a single email with a 10 MB `text/plain` body and an 8 MB `text/csv` attachment. Confirm the
body is fully present, the attachment `size_bytes` is the full length, and `content_hash` matches the
sha256 of the full payload (i.e. nothing was truncated or rejected).

## Expected
Body fully materialized (≥10 MB), attachment fully materialized (≥8 MB), hash matches → no cap.

## Execution result (2026-06-09)
```
[FAIL] C05_oversized_body_and_attachment_fully_materialized :: body_len=10485760 att_size=8388608 hash_match=True (no cap anywhere)
```
(`[FAIL]` = no cap was found; the "defence held" assertion was that a cap truncates/rejects.)

**Verdict:** ⚠️ Pass-with-concern — reproduced live. A 10 MB body + 8 MB attachment parsed and hashed
with zero limit; code review confirms no size check exists in `parse_email` /
`_extract_body_text` / `_attachment_bytes`. Demonstrated "no cap," deliberately not pushed toward OOM
on the shared container. Low severity (bounded by upstream IMAP fetch limits in practice, no
isolation impact) but a missing-guardrail that should land before the production fetch path streams
unbounded messages.

**Tag:** 🆕 NEW · Severity Low.
