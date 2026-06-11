# TC-IM-D05 — Attachment filename `../../etc/passwd` is stored verbatim but never used as a path

| Field | Value |
|---|---|
| **ID** | TC-IM-D05 · **Suite** D (Attachments) · **Type** Adversarial · **Mode** pure |
| **Result** | 📋 **Pass** (latent, no live impact) · **Tag** 📋 CONFIRMS-DOCUMENTED (CA-CONN-04) · **Severity if fail** High (path traversal / arbitrary file write) · **Status** Executed |
| **Harness** | `harness/attachment_suite.py` (`tc_d05_filename_path_traversal`) |

## Objective
Prove a path-traversal attachment filename (`../../../../etc/passwd`) survives parsing **verbatim** (it
is not sanitized for `../`) but is **never used as a filesystem path** today — so it is latent, not live.

## Break hypothesis
The filename is sanitized by `sanitize(part.get_filename(), MSGID_MAX)` in
`email_parser._extract_attachments`, which only NUL-strips and length-caps — it does **not** strip
`../`. If any code path wrote an attachment to disk using this filename, the traversal sequence would
let an attacker escape the intended directory (arbitrary file write / overwrite). The filename never
reaches `extract_text`, so the case is driven through `parse_email`.

## Steps (the harness)
1. Build a raw email with an attachment `filename="../../../../etc/passwd"`; `parse_email` it; assert
   `ParsedAttachment.filename` equals the traversal string **verbatim** (sanitize ≠ path-sanitize).
2. Build a second with a NUL-laced, 2500-char filename; assert NUL is stripped and length is capped to
   `MSGID_MAX` (998) with no crash.
3. **Code evidence (the LATENT proof):** the filename is stored as a plain column value
   (`email_ingest_service.py:155` → `filename=attachment.filename`), and a grep of the entire
   `backend/app/connectors/` tree for filesystem ops (`open(`/`Path(`/`os.path`/`.write(`/`shutil`/
   `aiofiles`) returns **zero matches** — no attachment filename ever reaches a path operation.

## Expected
Traversal retained verbatim; NUL stripped + length capped; filename used only as a stored string.

## Execution result (2026-06-09)
```
  [PASS] d05_traversal_retained_verbatim :: stored filename='../../../../etc/passwd' (sanitize = NUL+length only, no '../' stripping)
  [PASS] d05_nul_stripped_and_length_capped :: NUL absent & len=998 <= 998
```
Grep evidence (run from repo root, `backend/app/connectors/`):
```
pattern: \bopen\(|Path\(|os\.path|\.write\(|shutil|aiofiles|with open  -> No matches found
filename usage: email_ingest_service.py:155  filename=attachment.filename   (stored as a column value)
```
**Verdict — 📋 Pass (LATENT).** The traversal string is preserved unsanitized for `../`, but the entire
connectors module performs **no filesystem operations** — the filename only ever becomes a Postgres
text column value (`email_attachment.filename`). There is no live path-traversal: nothing writes the
attachment to disk under this name today. The NUL/length sanitization holds (no insert-breaking value
reaches a column).

**Tag — 📋 CONFIRMS-DOCUMENTED (CA-CONN-04).** This is a **gating risk** for CA-CONN-04 / the
`RawBlobStore` seam: the moment attachment bytes are persisted to a filesystem/object store keyed by
filename (or a binary extractor writes a temp file using `get_filename()`), this unsanitized `../`
becomes a live arbitrary-write/traversal. The fix when that lands: derive the storage key from the
content hash (already computed) — never the attacker-controlled filename — or path-sanitize
(basename + reject `..`). Latent today only because no path operation consumes it.
