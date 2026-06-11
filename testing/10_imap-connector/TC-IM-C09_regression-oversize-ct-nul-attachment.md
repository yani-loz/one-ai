# TC-IM-C09 — Regression: over-255 Content-Type + NUL in text attachment still ingests

| ID · Suite · Type · Mode |
|---|
| TC-IM-C09 · C (Parse & data quality) · Negative · ingest |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · ✔ CONFIRMS-FIXED · — · Executed |

## Objective (prove once)
Confirm the 7c90f55 fix holds: an attachment with an over-255-char Content-Type and a NUL inside a
text attachment no longer crashes the insert — the email still ingests.

## Break hypothesis (pre-fix)
A Content-Type longer than the `email_attachment.content_type` 255 column would error the insert;
a NUL in `extracted_text` would be rejected by Postgres text — either silently dropping the whole
email.

## Steps
Ingest a multipart email with (a) a `text/csv` attachment whose payload contains `x,y\x00z`, and
(b) an attachment whose Content-Type is ~601 chars. Read attachments back; assert the message stored
with both attachments, all content_types ≤255, no NUL in extracted_text.

## Expected
`STORED`, 2 attachments, every `content_type` ≤255, no NUL in any `extracted_text`.

## Execution result (2026-06-09)
```
[PASS] C09_oversize_ct_and_nul_attachment_still_ingests :: outcome=stored attachments=2 ct_capped=True no_nul_text=True
```

**Verdict:** ✅ Pass — fix holds live. `sanitize(..., CONTENT_TYPE_MAX)` caps the Content-Type and
`strip_nul` clears the NUL from the extracted text, so the malformed attachments are absorbed and the
email is not silently dropped.

**Tag:** ✔ CONFIRMS-FIXED.
