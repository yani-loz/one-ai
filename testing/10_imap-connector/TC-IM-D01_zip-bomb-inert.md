# TC-IM-D01 — Zip-bomb / decompression-bomb attachment is inert

| Field | Value |
|---|---|
| **ID** | TC-IM-D01 · **Suite** D (Attachments) · **Type** Adversarial · **Mode** pure |
| **Result** | 📋 **Pass** (defense held) · **Tag** 📋 CONFIRMS-DOCUMENTED (CA-CONN-04) · **Severity if fail** High (resource/DoS) · **Status** Executed |
| **Harness** | `harness/attachment_suite.py` (`tc_d01_zip_bomb`) |

## Objective
Prove a decompression-bomb attachment is **never decompressed** today — the text extractor drops it at
the text-only seam, so no resource amplification can occur.

## Break hypothesis
If `extract_text` (or any code on its path) decompressed an attachment to extract text, a tiny
`application/zip` blob that expands ~1000× would balloon memory/CPU (zip-bomb DoS). Adversarial twist:
even mislabel the bomb as `text/plain` to force it onto the `_decode_text` path.

## Steps (the harness)
1. `zlib.compress(b"\x00" * 1 MiB)` → a ~1 KB blob with ~1000× expansion ratio.
2. `extract_text` on it as **`application/zip`** → expect `None` (not text-like; bytes untouched).
3. `extract_text` on the **same blob mislabeled `text/plain`** → goes through `_decode_text` (raw
   `bytes.decode`); assert output length ≈ the *compressed* size, **not** the 1 MiB decompressed size.
4. Source check: `attachment_extractor.py` references no `zlib`/`gzip`/`zipfile`/`tarfile`.

## Expected
Dispatch `None` for `application/zip`; the mislabeled-text path returns a tiny string (no
decompression); no decompression library is reachable.

## Execution result (2026-06-09)
```
D01: 1 MiB-of-zeros compressed to 1039 bytes (expansion ~1009x)
  [PASS] d01_zip_dispatch_returns_none :: extract_text(application/zip)=None
  [PASS] d01_mislabeled_text_not_decompressed :: output_len=17 (compressed_in=1039, would-be-decompressed=1048576) -> no decompression
  [PASS] d01_extractor_imports_no_decompressor :: attachment_extractor.py references no zlib/gzip/zipfile/tarfile
```
**Verdict — 📋 Pass (INERT).** `application/zip` is not in `_TEXT_PREFIXES`/`_TEXT_EXACT`, so the bytes
return `None` and are dropped. Even mislabeled as text, `_decode_text` byte-decodes (output 17 chars,
not 1 MiB) — never `zlib.decompress`. The discriminating check (output size vs decompressed size) would
have FAILED if any decompression occurred; it passed.

**Tag — 📋 CONFIRMS-DOCUMENTED (CA-CONN-04).** This is the **gating risk** for when binary extraction
lands (`docs/FIX_BEFORE_PROD.md`, CA-CONN-04 — PDF/docx/xlsx/pptx/TNEF extractors + RawBlobStore). A
zip/archive or nested-archive extractor MUST enforce an expansion-ratio + output-size cap before
decompressing, or this latent DoS becomes live. Inert today only because nothing decompresses.
