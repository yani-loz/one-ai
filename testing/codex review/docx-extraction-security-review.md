# Codex Review: DOCX Extraction ZIP-Bomb Findings

Date: 2026-06-12

## Scope

This report covers review findings for the DOCX extraction path used by IMAP attachment
parsing.

Reviewed file:

- `backend/app/connectors/imap/parsing/extractors/docx.py`

## Executive Summary

The DOCX extraction path adds useful attachment parsing functionality, but the current
ZIP-bomb protections are incomplete for untrusted DOCX files.

Two resource-exhaustion risks remain:

- Inflated ZIP member size is not bounded while data is being decompressed.
- ZIP member count is checked only after `ZipFile(...)` has already parsed the central
  directory and allocated `ZipInfo` objects.

These gaps can allow crafted DOCX attachments below the configured attachment size limit
to consume excessive worker memory or CPU.

## Finding 1: DOCX ZIP Bounds Are Not Enforced During Inflation

Severity: P1

File:

- `backend/app/connectors/imap/parsing/extractors/docx.py`

Issue:

The implementation checks ZIP member size using `ZipInfo.file_size`. This value is
attacker-controlled metadata. A crafted DOCX can declare a small `file_size` for a
compressed XML part, pass the current metadata checks, and then expand to a much larger
payload when `python-docx` or `archive.read()` inflates the member.

In that case, the worker can allocate or process the inflated data before the ZIP CRC
failure is detected.

Impact:

- A malicious DOCX attachment can exhaust ingest-worker memory.
- The configured attachment and ZIP-size limits can appear to pass while real inflated
  bytes are still unbounded.
- CRC validation happens too late to be relied on as a resource protection.

Recommended fix:

- Enforce maximum decompressed bytes while reading each ZIP member.
- Prefer streaming reads with a hard byte counter over `archive.read()` for XML parts.
- Abort as soon as the inflated byte count exceeds the configured DOCX/XML-part limit.
- Ensure all DOCX parsing paths, including `python-docx` handoff paths, are covered by
  the same bounded-inflation guard or by a prevalidated bounded copy.

Suggested test coverage:

- Construct a DOCX-like ZIP where metadata advertises a small member size but inflation
  exceeds the limit.
- Assert extraction fails before reading the full inflated payload into memory.
- Assert the public error is sanitized and does not leak parser internals.

Acceptance criteria:

- Declared ZIP metadata is not trusted as the only size bound.
- No DOCX member can inflate past the configured per-part or total DOCX parse limit.
- Oversized inflated members fail deterministically without worker memory exhaustion.

## Finding 2: ZIP Member Count Is Checked Too Late

Severity: P2

File:

- `backend/app/connectors/imap/parsing/extractors/docx.py`

Issue:

The implementation checks `MAX_ZIP_MEMBERS` only after constructing `ZipFile(...)`.
However, `ZipFile(...)` reads the central directory and builds `ZipInfo` objects for
members before later application-level validation runs.

A crafted DOCX with hundreds of thousands of empty or tiny ZIP entries can therefore
consume significant memory and CPU before the member-count guard is reached.

Impact:

- A small compressed attachment can cause high memory allocation during central-directory
  parsing.
- The ingest worker can spend meaningful CPU and memory before rejecting the attachment.
- The advertised member-count protection does not fully protect the expensive operation
  it is meant to bound.

Recommended fix:

- Precheck the ZIP central directory or EOCD member count before constructing the full
  `ZipFile`.
- Reject files whose central-directory entry count exceeds `MAX_ZIP_MEMBERS`.
- Also bound central-directory size and parsing work where practical.
- Keep the existing post-open validation as a defense-in-depth check.

Suggested test coverage:

- Construct a ZIP with a member count above `MAX_ZIP_MEMBERS`.
- Assert the parser rejects it before full `ZipFile` member allocation.
- Include a valid DOCX fixture below the threshold to confirm normal extraction still
  succeeds.

Acceptance criteria:

- DOCX files with excessive member counts are rejected before full `ZipFile`
  construction.
- Central-directory parsing cannot allocate unbounded `ZipInfo` objects.
- Existing valid DOCX extraction behavior remains unchanged.

## Overall Remediation Target

The DOCX extractor should enforce limits before and during the expensive ZIP operations:

- Preflight ZIP structure and member count before full archive loading.
- Bound decompressed bytes while inflating members.
- Preserve existing attachment-size checks as an outer limit.
- Add adversarial ZIP fixtures that prove metadata-only limits cannot be bypassed.
