# TC-IM-C04 — C0 control-char survival (all but NUL) into subject/body/headers

| ID · Suite · Type · Mode |
|---|
| TC-IM-C04 · C (Parse & data quality) · Fuzz · pure + ingest |

| Result · Tag · Severity · Status |
|---|
| ⚠️ Pass-with-concern · 🆕 NEW · Low · Executed |

## Objective
Show that every C0 control char **except NUL** survives parsing and persists into the stored
`subject` / `body_text` (and header values) — a log/terminal-injection and dirty-downstream-text
vector.

## Break hypothesis
The only character class scrubbed anywhere in the parse path is NUL (`strip_nul`, headers.py:123-125,
because Postgres text/jsonb reject it). `\x01` (SOH), `\x07` (BEL), and `\x1b` (ESC, the ANSI escape
introducer) are never stripped, so they pass through into the parsed fields and then into the DB
columns. A subject/body rendered to a terminal log or an unsanitised UI can be hijacked by ANSI
escapes.

## Steps
1. Pure: parse an email whose subject + body embed `\x1b[31m…`, `\x07`, `\x01`. Assert the controls
   survive in `parsed.subject` / `parsed.body_text`.
2. Ingest: store the same email and read the row back from the DB; assert the controls persisted.

## Expected
Controls present (only NUL absent) both in the parsed object and the stored columns; the email
ingests fine.

## Execution result (2026-06-09)

Pure:
```
[FAIL] C04_c0_controls_survive_body :: body controls present=True body='alert\x07 esc\x1b[31mRED\x1b[0m soh\x01end'
[FAIL] C04_c0_controls_survive_subject_and_headers :: subject='hdr\x1b[31minj\x01ect'
```
Ingest (read back from DB):
```
[FAIL] C04_control_chars_persisted_into_db_columns :: outcome=stored body_has_esc/bel=True subj_has_esc=True subject='ev\x1b[31mil\x01'
```
(`[FAIL]` = the controls survived; the defence-held assertion was negative.)

**Verdict:** ⚠️ Pass-with-concern — reproduced live, both in-memory and persisted. NUL handling is
correct; all other C0 controls (incl. ANSI escape sequences) flow verbatim into the database. Low
severity (no isolation breach), but a real injection surface for any log/terminal/UI consumer of
`subject`/`body_text`. Mitigation: strip/escape the C0 range (except `\t`/`\n`) at the sanitize seam.

**Tag:** 🆕 NEW · Severity Low.
