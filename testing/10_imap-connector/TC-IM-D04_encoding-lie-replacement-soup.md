# TC-IM-D04 — Binary blob mislabeled `text/plain` → replacement-char soup (no sniffing)

| Field | Value |
|---|---|
| **ID** | TC-IM-D04 · **Suite** D (Attachments) · **Type** Fuzz · **Mode** pure |
| **Result** | ⚠️ **Pass-with-concern** · **Tag** 🆕 NEW · **Severity** Info (data-quality only) · **Status** Executed |
| **Harness** | `harness/attachment_suite.py` (`tc_d04_encoding_lie`) |

## Objective
Show that a binary blob (a PNG + raw bytes) mislabeled `Content-Type: text/plain` is decoded as text
without crashing, but produces **replacement-char soup** because the Content-Type is trusted with no
content sniffing.

## Break hypothesis
The extractor dispatches purely on the declared `content_type`. A binary attachment relabeled
`text/plain` therefore goes through `_decode_text` → `payload.decode("utf-8", errors="replace")`. The
"never raises" contract means **no crash** (that's the contract holding — a Pass), but the stored
`extracted_text` is corrupt U+FFFD soup the runner would persist. There is no content-type sniffing /
magic-byte validation to reject the lie.

## Steps (the harness)
1. `extract_text` on `b"\x89PNG\r\n\x1a\n" + bytes(range(256))*4` labeled `text/plain`.
2. Assert it returns a `str` (no exception — contract holds).
3. Assert the result contains U+FFFD replacement chars (the data-quality observation).

## Expected
A `str` of replacement-char soup, no exception.

## Execution result (2026-06-09)
```
  [PASS] d04_mislabeled_binary_no_crash :: returned str of len 1028 (no exception)
  [PASS] d04_output_is_replacement_soup :: U+FFFD replacement chars present: 513 (no content sniffing -> trusted Content-Type)
```
**Verdict — ⚠️ Pass-with-concern.** The robustness contract **holds** (no crash, best-effort decode by
design — `attachment_extractor.py` docstring: "errors='replace', best effort"). The concern is purely
**data quality**: 513 U+FFFD chars of garbage would be written to `email_attachment.extracted_text`
because the declared Content-Type is trusted and never sniffed/validated against the bytes. This is not
a security defect (no crash, no injection, no cross-tenant exposure) — it pollutes search/retrieval
quality with noise rows.

**Scope honesty (pure mode):** this proves `extract_text` *returns* soup that the runner *would* store;
it does not itself write a row. The persistence is by design (extractor output → `extracted_text`).

**Tag — 🆕 NEW (Info).** Not tracked in `docs/FIX_BEFORE_PROD.md` (CA-CONN-04 covers *missing* binary
text, not *mislabeled-binary soup* on the text path). Cheap mitigation when CA-CONN-04 lands: a quick
printable-ratio / magic-byte check that routes obviously-binary `text/*` payloads to the binary seam (or
suppresses an all-replacement-char result to `None`) so retrieval isn't polluted. Info severity.
