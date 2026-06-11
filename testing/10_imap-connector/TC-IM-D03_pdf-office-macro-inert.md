# TC-IM-D03 — PDF/Office attachment with a macro/exploit is inert

| Field | Value |
|---|---|
| **ID** | TC-IM-D03 · **Suite** D (Attachments) · **Type** Adversarial · **Mode** pure |
| **Result** | 📋 **Pass** (defense held) · **Tag** 📋 CONFIRMS-DOCUMENTED (CA-CONN-04) · **Severity if fail** High (RCE / macro execution via a doc parser) · **Status** Executed |
| **Harness** | `harness/attachment_suite.py` (`tc_d03_pdf_office_macro`) |

## Objective
Prove a malicious PDF (`/OpenAction` JavaScript) or Office doc (auto-exec macro / `winmail.dat` TNEF)
attachment is **never opened or parsed** — `extract_text` returns `None`, bytes are dropped.

## Break hypothesis
If a PDF/Office parser ran to extract text, a crafted document's `/OpenAction`/`AutoOpen`/VBA macro
could trigger parser exploits or be the first stage of a content-extraction RCE. Plain reading: these
content-types are not text-like → `None`, bytes never opened.

## Steps (the harness)
1. `extract_text` on five binary types: a malicious PDF (`/OpenAction /JavaScript`), an OOXML `.docx`
   (`vbaProject.bin`), `application/vnd.ms-excel`, `application/msword`, and `application/ms-tnef`
   (the 261-count Outlook `winmail.dat` from the real corpus) → all expect `None`.
2. **Positive control**: a benign `text/plain` extracts correctly (so `None` is a real dispatch
   decision, not a broken function).
3. Source check: `attachment_extractor.py` references no `pypdf`/`fitz`/`docx`/`openpyxl`/`pptx`/`olefile`/`tnef`.

## Expected
All five binary types → `None`; the control text extracts; no document library reachable.

## Execution result (2026-06-09)
```
  [PASS] d03_binary_office_pdf_all_none :: pdf=None; vnd.openxmlf=None; vnd.ms-excel=None; msword=None; ms-tnef=None
  [PASS] d03_positive_control_text_extracts :: control='benign control text'
  [PASS] d03_extractor_imports_no_doc_lib :: attachment_extractor.py references no PDF/docx/xlsx/pptx/ole/tnef parser
```
**Verdict — 📋 Pass (INERT).** Every binary document type falls through to `None`; the positive control
confirms the function works (the `None` is dispatch, not breakage); no document parser is reachable, so
malicious bytes are never opened.

**Tag — 📋 CONFIRMS-DOCUMENTED (CA-CONN-04).** CA-CONN-04's real-corpus measurement (281 PDF, 180
.docx, ~30 pptx/xlsx, 261 TNEF) is exactly this set — these are the formats the deferred extractor will
add. When it lands, each per-format parser must run sandboxed / with macro & JS execution disabled and
resource caps. Inert today only because nothing opens the bytes.
