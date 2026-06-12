# Connect — Attachment Content Extraction — Design

> **Status: PROPOSAL — for review. Nothing here is implemented.**
> Closes the CA-CONN-04 gate (`docs/FIX_BEFORE_PROD.md`): binary attachment text extraction MUST land
> before any production path discards attachment bytes after parse.
>
> Inputs: (1) a full quantitative census of the dev-mailbox attachment corpus (13,635 `.eml` →
> 20,512 MIME parts → ~10,096 content-hash-deduplicated `email_attachment` rows, seeded sampling),
> (2) the prior CON-04 / CON-04-ext research digests, (3) a verified 2026-06 library/license/OCR
> landscape survey. Numbers below are the census's, not estimates, unless marked "~".
>
> Binding constraints (settled elsewhere, not re-litigated here):
> - **GDPR / data sovereignty:** no tenant content leaves the deployment without a zero-retention
>   agreement; local-first extraction (Project Bible; ingestion design §6).
> - **No AGPL dependencies** (commercial product). PyMuPDF is excluded; so is Marker (see §3.4).
> - **The extractor seam** (`backend/app/connectors/imap/parsing/attachment_extractor.py`):
>   `extract_text(ParsedAttachment) -> str | None`, NEVER raises, NUL-stripped output, blank → None,
>   and **honest NULL** — None means "no text extracted (yet)", never fake/empty text.
> - **Lean attachments (ingestion design §4, DECIDED):** text extracted inline at parse, original
>   bytes discarded; `RawBlobStore` seam kept unimplemented as the escape hatch (§1, §9).
> - **Deterministic, no LLM at parse** (ingestion design §4 heading).
> - Packaging: `uv` + the dockerized backend image; heavyweight tooling goes in worker images, not
>   the API image.

---

## 1. Goal and the measured gap

**Goal:** make `email_attachment.extracted_text` non-NULL for every attachment that actually
contains text, with an explicit machine-readable reason recorded for every one that doesn't.

Today the seam extracts only `text/*` + {json, xml, csv, `application/text`, `message/rfc822`} —
**~197 of ~10,096 rows (≈2.0%)**. Everything binary is honest-NULL. The census shows what's inside
the other 98%:

| Type | DB rows | % rows | Volume | Extractable today | What's inside (census findings) |
|---|---:|---:|---:|---|---|
| Images (png/jpeg/gif/svg…) | 5,996 | 59.4% | — | No | 58.5% <50 KB signature/logo class (2,656 <10 KB); 97.5% have `content_id`, 83% named `image\d+.*` (Outlook-embedded); >300 KB "photo class" = 1,251 rows but only **119 distinct hashes** (one 1.69 MB signature banner ≈ 1,070 rows); **0 TIFF, 0 scanned-document pages stored as images** |
| TNEF (`winmail.dat`) | 1,560 | 15.5% | 574 MB | No | **99% carry the email's RTF body** (skipping TNEF loses body text, not just attachments); 43% contain embedded files (mostly `image001.png` signatures + `invite.ics`); only ~7% of containers hold a real document (pdf/docx/xlsx) |
| PDF | 1,075 | 10.6% | 1,162 MB | No | **95 : 5 : 0 : 0** born-digital : scanned : encrypted : broken (100-sample, all valid `%PDF`); language BG 71% / EN 29% / DE 0%; content = NDAs, contracts, invoices, reports. The 5% scanned are **exactly the accounting/payroll docs** (Vedomost, income-tax calcs), 1–30 pp, 160 KB–3.5 MB |
| docx | 845 | 8.4% | — | No | 60/60 sampled valid OOXML zip |
| Text-shaped (txt/html/md/json/ics/…) | ~217 | 2.1% | — | Mostly (197) | `.md`, `.txt`, `.html`, `.json`, invites; **`.env` files attached** (secrets-scan flag, §6); `application/ics` (10) currently falls through to NULL |
| `application/octet-stream` | 102 | 1.0% | — | No | Sniffed: **~64% archives** (rar 31, zip 15+2 corrupt/split, 7z 2), ~19% text/markdown/notebooks, ~9% mislabeled office/pdf, 5 `.emz` gzip graphics, 1 font, **0 executables** |
| xlsx | 63 | 0.6% | — | No | 40/40 valid; +1 xlsm, +2 ODF |
| Legacy `.doc` / `.xls` | 63 | 0.6% | — | No | 31 real OLE .doc + 20 real OLE .xls = **~51 real legacy binaries**; 6 mislabeled text, 2 actually-xlsx, 3 unreadable; **0 encrypted Office files in the entire 188-file sample** |
| zip (`x-zip-compressed`) | 46 | 0.5% | 173 MB | No | archives |
| Audio (`audio/mpeg`) | 30 | 0.3% | 76 MB | No | .mp3/.m4a — CON-03 transcription candidates |
| pptx | 30 | 0.3% | — | No | 25/25 valid |
| Noise (delivery-status, p7s, rfc822-fragments) | ~42 | 0.4% | — | partial | bounce reports, S/MIME signatures |

**Document-bearing denominator.** Rows that plausibly contain extractable text (PDF + TNEF + Office
+ legacy + text-shaped + the office/text slice of octet-stream) ≈ **3,870 rows**. Today's coverage of
that denominator: **~5%**. This proposal takes it to **~98%** (the residue: ~51 legacy OLE files and
the corrupt tail, all explicitly status-marked). The 59% of rows that are images are *correctly*
skipped — the census found zero documents hiding in them.

---

## 2. Per content type — proposals

All extractors live behind the existing `extract_text` seam and inherit its invariants: never raise,
NUL-strip, blank → None. One seam-contract extension is proposed (flagged for review): the seam
returns a small `ExtractionResult(text: str | None, status: ExtractionStatus, detail: str | None)`
instead of bare `str | None`, and `email_attachment` gains an **`extraction_status`** column
(+ `extractor_name`, `extractor_version`, `ocr_confidence` — nullable) so honest NULL becomes
honest-NULL-with-a-reason and future backfills can target exactly the rows a better extractor would
improve. Proposed enum: `extracted · extracted_ocr · empty · encrypted · corrupt · low_confidence ·
unsupported_format · unsupported_legacy_format · skipped_inline_image · skipped_image ·
skipped_archive · skipped_noise · skipped_too_large · deferred_transcription · pending_ocr`.

A global guard applies to every path: payloads above a size ceiling (proposed 50 MB) are not parsed
→ `skipped_too_large` (bounded memory, never-raise preserved).

### 2.1 Born-digital PDF (~1,021 rows — the single biggest win)

- **Primary: `pdfplumber`** (MIT) — per CON-04 §9; best layout + table extraction, BG/Cyrillic OK.
- **Fallback: `pypdf`** (BSD-style) — some malformed PDFs that crash pdfminer.six parse under pypdf;
  also the cheap probe for encryption/page count (§3.1).
- **Excluded: PyMuPDF** — AGPL-3.0, commercial license $1.5k–$50k+/yr. Hard no (CON-04 agrees).
- Tables: serialize `extract_tables()` cells as tab-separated lines appended after page text — the
  stated reason CON-04 chose pdfplumber is its table strength; plain `extract_text` alone would
  under-use it (flagged divergence-avoidance: we keep the table payoff, but structured-JSON table
  storage is deferred to the financial-extraction pipeline, CON-04-ext).
- Failure handling: parse exception in pdfplumber → retry with pypdf → still failing →
  `corrupt`, text NULL. Pages with no text layer in an otherwise-digital PDF → hybrid handling (§3.1).
- Tests: corpus-derived fixtures (small synthetic BG+EN PDFs committed), truncated-byte and
  garbage-byte never-raise tests, NUL/blank handling, a marked integration suite that runs the real
  disk dump.
- Coverage gain: **+~1,021 rows (+10.1% of all rows)**; ~88% of PDF bytes (1,162 MB type).

### 2.2 Scanned PDF (~54 rows) — see §3 (own strategy section)

Local Tesseract OCR, confidence-gated, queued not inline. Census: only ~5% of PDFs, but they are
precisely the accounting/payroll documents — low row count, high business value.

### 2.3 Encrypted PDF (0 rows in census)

- Probe: `pypdf.is_encrypted` → attempt `decrypt("")` (owner-password-only locks are common and
  legally fine to open — the user received the file).
- Real password → **`encrypted`, text NULL**. No password vault, no UI, no brute force at MVP.
- Census found zero, so this is policy insurance, not a workload. Policy sign-off in §6.

### 2.4 docx (845 rows)

- **Primary: `python-docx`** (MIT, per CON-04). Extract paragraphs in order + table cells
  (row-serialized) + headers/footers.
- **Fallback:** raw `zipfile` + `word/document.xml` XML text-node walk (stdlib) — emergency path for
  OOXML zips python-docx rejects.
- Failure: not-a-zip / corrupt → `corrupt`. Census says 60/60 valid, so expect ~0.
- Tests: fixture .docx with BG text + table; truncated zip; macro-enabled variants pass through
  unchanged (we read XML, never execute anything).
- Gain: **+845 rows (+8.4%)**.

### 2.5 xlsx (63 rows, +1 xlsm, +2 ODF)

- **Primary: `openpyxl`** (MIT) in **read-only mode** (bounded memory). Per CON-04's key strategy:
  don't dump raw cells — serialize each row as `header: value` natural-language lines per sheet,
  sheet name as a heading. This is what makes spreadsheet text retrievable downstream.
- xlsm: same reader (macros ignored, never executed). ODF (2 rows): `unsupported_format` at MVP.
- Fallback: none real (pandas is openpyxl underneath) → failure = `corrupt`, honest NULL.
- Gain: **+~64 rows (+0.6%)** — small count, but spreadsheets are dense (offers, budgets).

### 2.6 pptx (30 rows)

- **Primary: `python-pptx`** (MIT). Slide text boxes + titles + tables + **speaker notes** (CON-04:
  "often richest content"), one chunk per slide with slide number in the text.
- Failure → `corrupt`. Gain: **+30 rows (+0.3%)**.

### 2.7 Legacy `.doc` / `.xls` (~51 real OLE binaries)

- **MVP: honest NULL + `unsupported_legacy_format`.** No permissive pure-Python `.doc` text
  extractor exists (olefile reads streams, not Word text; antiword is GPL).
- The 6 mislabeled-as-legacy plain-text files and 2 actually-xlsx files are rescued by content
  sniffing (§2.10 logic applied when OLE magic is absent).
- Later option (separate decision, §6): a **LibreOffice-headless sidecar** (`soffice --headless
  --convert-to docx`, MPL-2.0) — works but adds >1 GB, so it would be its own worker image, never in
  the API image. `xlrd` 2.x (BSD) is a cheaper `.xls`-only option worth verifying first.
- Deferred rows are exactly recoverable later **only** if bytes are retained for this class (§4.3).

### 2.8 RTF (standalone + TNEF bodies)

- **Primary: `striprtf`** (BSD-3) — tiny, zero-dep, feature-complete. Maintenance is flagged
  inactive (Snyk); accepted — pin the version and wrap behind the extractor adapter.
- **Plus `compressed-rtf`** (MIT): TNEF carries LZFu/MELA-compressed RTF; decompress before striprtf.
- Fallback: `rtfparse` if structured traversal ever becomes necessary (not now).
- Standalone `application/rtf` rows are near-zero in the census; the payload here is §2.9.

### 2.9 TNEF / `winmail.dat` (1,560 rows, 574 MB) — body carrier first, container second

- **Primary: `tnefparse`** — **already a project dependency** (`backend/pyproject.toml`,
  `tnefparse>=1.4.0`) and already exercised against the corpus in `email_parser.py` for interior
  dedup digests ("verified stable across all 271 multi-copy TNEF groups"). License: **LGPL-3.0 —
  import-only is fine commercially** (unlike AGPL); do not vendor/modify. Maintenance inactive →
  pin + adapter-wrap + corpus regression tests so a future swap is cheap. No better-maintained
  permissive alternative exists (pytnef wraps a C binary; libytnef is GPL).
- Extraction = two layers:
  1. **RTF body (99% of containers):** decompress (`compressed-rtf`) → strip (`striprtf`) → this is
     the *email body*, not an attachment in spirit; it becomes the TNEF row's `extracted_text`.
  2. **Recursion into embedded files (43% of containers):** each embedded file is content-sniffed
     and dispatched back through the same per-format extractors; extracted text is appended under a
     `--- embedded: <name> ---` marker. Signature `image00N.png` embeds are skipped by the image
     rules (§2.11); `invite.ics` decodes as text. Real-document yield is low (~7% of containers ≈
     ~110 docs corpus-wide) but non-zero.
- Failure: tnefparse "raises diverse internals on corrupt blobs" (existing precedent in
  `email_parser.py`) — catch-all → degrade per layer, never fail; fully empty container → `empty`.
- Gain: **+~1,544 rows (+15.3%)** — the second-largest single win, and it is *body text*.
- Flagged: CON-04 never covered TNEF; this extends it (the corpus made the case — 15.5% of rows).

### 2.10 `application/octet-stream` (102 rows) — sniff, then dispatch

Never trust the declared type; sniff magic bytes:

| Magic | Dispatch |
|---|---|
| `%PDF` | PDF path (§2.1/§3) — rescues the ~2 mislabeled PDFs |
| `PK..` + `[Content_Types].xml` | OOXML → docx/xlsx/pptx extractor (rescues ~5 mislabeled docx) |
| `PK..` plain / `Rar!` / `7z` / split `.zip.00N` | `skipped_archive` (no recursion — §2.13) |
| gzip (the `image004.emz` graphics) | `skipped_noise` |
| OLE (`D0 CF 11 E0`) | legacy path (§2.7) |
| decodes cleanly as UTF-8/CP1251 text (json/ipynb/md/CAD-text) | text path |
| else | `unsupported_format` |

Gain: **+~28 rows** (the ~9% mislabeled office/pdf + ~19% text/notebooks). Also fold
`application/ics` (10 rows) into the text-exact set — a one-line fix the census exposed.

### 2.11 Images (5,996 rows) — skip the swamp, OCR nothing (yet)

Three classes, three policies (heuristics straight from the census):

- **Signature/inline class — SKIP** (`skipped_inline_image`): `content_id` present (97.5% of all
  image rows) OR filename matches `image\d+.*` (83%) OR size <50 KB (58.5%). Logos, banners,
  Outlook embeds. Zero text value; OCR-ing them would generate garbage tokens at scale.
- **Scan-class — currently EMPTY:** the deduplicated large-image sample found **zero scanned A4
  document pages stored as images** (and 0 TIFF corpus-wide). Proposal: **no image OCR at MVP**;
  every non-inline image gets `skipped_image`. If a future tenant corpus shows document scans (the
  census script is reusable per tenant), enable a Phase C gate: large + not-inline + page-like
  aspect ratio + low color saturation → route into the same OCR queue as scanned PDFs (§3). This is
  an open decision (§6) — the data says don't build it yet.
- **Photo class — SKIP/DEFER** (`skipped_image`): 12/22 sampled large images are phone photos;
  captioning/vision is an Ask/Learn-layer concern (LLM, not deterministic parse) and is explicitly
  out of this seam's scope.

Dedup makes the whole class cheap: 1,251 large rows collapse to 119 distinct hashes.
Gain: 0 rows — and that is the *correct* number per the data.

### 2.12 Audio / video (30 rows, 76 MB)

**Defer to CON-03** (audio/video transcription connector — Whisper/AssemblyAI/Gemini survey,
diarization, BG support). Mark `deferred_transcription`. Transcription is a different pipeline
(ASR, minutes-per-file, provider decisions) — it must not block document extraction. Note: under
lean-attachments the bytes will be gone; CON-03 over *email* audio needs the RawBlobStore retention
class (§4.3) or a re-fetch.

### 2.13 Everything else — explicit skip list

| Type | Status | Why |
|---|---|---|
| Archives: zip / `x-zip-compressed` (46, 173 MB) / rar / 7z / split `.zip.00N` | `skipped_archive` | 64% of octet-stream + dedicated zip rows. No recursion at MVP: zip-bomb / path-traversal surface, unbounded recursion, low expected yield. Revisit with hard guards if a tenant needs it (§6). Split archives are individually unrecoverable regardless. |
| `application/pkcs7-signature` (14) | `skipped_noise` | S/MIME signatures |
| `message/delivery-status` (17) | `skipped_noise` | bounce machinery |
| Fonts (`.ttc`), `.emz` graphics | `skipped_noise` | no text |
| svg / heic / jfif (3) | `skipped_image` | (HEIC unreadable by stock Pillow anyway) |

---

## 3. The SCANNED strategy

### 3.1 Detection heuristic (cheap probe, runs on every PDF)

On the first `min(5, n_pages)` pages via pypdf/pdfplumber:

1. `is_encrypted` → try `decrypt("")`; real password → `encrypted` (§2.3).
2. Per-page text length: avg **>100 chars/page → born-digital** (extract inline, §2.1);
   **<20 chars/page AND a page-covering image XObject (>80% page area) → scanned** (enqueue, §3.3);
   **in between → hybrid**: extract the text-layer pages inline, enqueue only the imageless pages,
   merge in page order at job completion.
3. A text layer that is garbage (high replacement-char / non-printable ratio — broken CMaps) is
   treated as scanned.
4. Parse exception → retry with the other library → `corrupt`.

### 3.2 Engine: Tesseract 5 — local, Apache-2.0 end-to-end

- **Primary: Tesseract 5 + `pytesseract`** (Apache-2.0 code AND traineddata — `tessdata_best` is
  Apache-2.0). Language packs: **`bul` + `deu` + `eng`** — exactly the corpus mix (BG 71% / EN 29%,
  DE 0% today but DACH is the market). Pass `lang="bul+deu+eng"`; refine per-document via langdetect
  on any text-layer fragment when present.
- **Fallback / second pass: RapidOCR** (Apache-2.0 code + converted PP-OCR ONNX weights; Cyrillic
  covered; ONNX Runtime only — no PyTorch/Paddle in the image). Used to re-attempt
  `low_confidence` pages before they go to HiTL.
- **Rasterization: `pypdfium2`** (Apache/BSD) — keeps the path subprocess-free; poppler `pdftoppm`
  (GPL, subprocess-invoked) is the legal-but-uglier alternative.
- **Rejected:** EasyOCR (weights license unstated, stale since 2024); PaddleOCR direct (the Paddle
  framework is a heavy Docker dependency — RapidOCR serves the same models without it);
  **Marker** — see §3.4.
- **Docker / uv packaging:** `apt-get install -y tesseract-ocr tesseract-ocr-bul tesseract-ocr-deu
  tesseract-ocr-eng` (~30 MB) in the **worker** image; for max accuracy, COPY pinned
  `bul/deu/eng.traineddata` from `tessdata_best` into `/usr/share/tesseract-ocr/5/tessdata/`.
  Python deps (`pytesseract`, `pypdfium2`, `rapidocr`) via `uv` in `backend/pyproject.toml`,
  grouped so the API image can exclude the OCR extras.
- **Validation before trust:** no published BG benchmark exists for Tesseract's `bul` model — run a
  **50-doc eval on real BG scans** (the census's 5% slice) and record it in
  `docs/experiments/NOTEBOOK.md` before freezing the threshold.

### 3.3 Confidence gate → honest NULL + HiTL

- Record **per-page mean confidence**; store doc-level mean in `ocr_confidence`.
- **≥ threshold** (proposed start: 60, to be calibrated by the 50-doc eval) → `extracted_ocr`.
- **< threshold** → **text stays NULL**, `extraction_status='low_confidence'` — honestly absent,
  never garbled text polluting search/embeddings. These rows are the **HiTL queue**: surfaced in
  the existing review pattern (ingestion design §6's "pit" precedent) for a human to read/transcribe
  or approve escalation.
- This mirrors CON-04-ext's own line: "below ~80% OCR confidence — flagged for human entry."

### 3.4 Why cloud / LLM OCR is deferred (and what would change that)

**GDPR posture:** sending tenant documents to any OCR API is tenant-content egress. Anthropic
zero-data-retention is an enterprise agreement subject to approval (standard API retains ~7 days) —
One AI does not have one yet; OpenAI ZDR is likewise per-org via sales; Google Document AI has an EU
endpoint **but** some processor versions route through the global Vertex endpoint and are explicitly
not residency-compliant. Azure Document Intelligence (EU region, 24 h max retention, no training) is
the most defensible — but "local, zero egress" is the sovereignty pitch to DACH customers, and ~54
scanned docs do not justify weakening it. **Cost** is real but secondary: Azure DI ≈ $1.50/1k pages;
Claude Haiku vision ≈ $0.005/page — cheap, just not the point.

**License correction (deliberate divergence from CON-04):** CON-04 recommended "Marker or docTR —
Apache 2.0" for scanned PDFs. Verified 2026-06: **Marker's code is GPL-3.0 and its weights are
modified-OpenRAIL-M (free only for research/personal/<$2M startups; paid datalab.to license
beyond)** — disqualified under CON-04's own AGPL-poison-pill logic. We choose **Tesseract** (which
aligns with CON-04-ext's "OCR fallback via Tesseract") with RapidOCR as the modern local fallback;
docTR (genuinely Apache-2.0) remains a future local option if RapidOCR underperforms.

**Reconsider cloud/LLM OCR when ALL of:** (1) a signed ZDR (Anthropic) or an EU-region DPA
(Azure DI) is in place, (2) it is **per-tenant opt-in**, surfaced in the tenant's data-processing
settings, (3) the local engines measurably fail the 50-doc BG eval (e.g. degraded scans /
handwriting), and (4) it lands as the §3.3 escalation tier — never the default path.

---

## 4. Pipeline architecture

### 4.1 Inline for cheap formats, queued for OCR (hybrid — a flagged divergence)

The tech survey recommended a fully async extraction queue. **Proposal: hybrid instead.**

- **Inline at the existing seam** for everything in §2 except OCR: text decode, PDF text-layer,
  Office, RTF, TNEF, sniffing all run at **~10–100 ms/doc** — they fit inside the parse step without
  threatening IMAP socket timeouts, keep the lean-attachments invariant intact (extract → discard
  bytes, atomically, in one place), and avoid a queue for the 95% case. Content-hash dedup
  (disk:DB ≈ 2.03×) means each distinct blob is extracted once.
- **Queued for OCR only:** OCR is 1–3 s/page/core — inline would stall sync runs. The §3.1 probe
  (cheap, inline) classifies; scanned/hybrid pages produce `extraction_status='pending_ocr'`, the
  bytes for *those attachments only* go to the RawBlobStore (§4.3), and a dedicated extraction
  worker — reusing the existing SyncRunner job pattern — drains the queue, writes `extracted_text` +
  `extracted_ocr`/`low_confidence`, then **deletes the retained bytes**. Idempotent on content hash.

### 4.2 Backfill for the existing corpus (dev)

The full dev corpus still exists on disk: `spikes/imap_dump/yani.lozanov@ethera-tech.com/`
(13,635 `.eml`, 0 parse failures). Backfill = a one-shot script that re-reads the `.eml` files,
walks MIME parts, matches `email_attachment` rows by content hash, runs each distinct hash through
the new extractors once, and updates `extracted_text` + `extraction_status`. No IMAP re-fetch
needed. Measured-cost estimate (tech survey): text-layer pass well under 1 h with a 6–8-process
pool; OCR pass ~3–11 CPU-hours → ~0.5–1.5 h wall on 8 cores; Office/RTF/TNEF <15 min — **an evening
on a dev machine (~2–5 h wall), OCR-dominated**. (Per the connect-ingest memory: run it ALONE, not
alongside pytest, which wipes the graph.)

### 4.3 Production implication — extraction MUST precede byte-discard

This is the CA-CONN-04 gate restated as architecture:

- The production SyncRunner may discard attachment bytes **only after** the inline extractors have
  run. A format the inline path can't handle is *permanently* lost the moment bytes drop — the IMAP
  server is the only recourse, and only while the mail still exists there.
- **RawBlobStore graduates from seam to minimal implementation** — but *narrow and transient*:
  it retains bytes **only** for (a) `pending_ocr` attachments until the OCR worker finishes
  (then deletes), and (b) optionally the deferred classes (`unsupported_legacy_format`,
  `deferred_transcription`, `encrypted`) **if** Yani opts into deferred-class retention (§6) so a
  future LibreOffice sidecar / CON-03 / password flow can backfill without re-fetch. Backend:
  local volume now, EU-sovereign MinIO behind the same interface later — exactly the escape hatch
  the ingestion design (§1) reserved. GDPR data-minimisation holds: retention is scoped per class,
  transient where possible, and erased by the same connector-delete lifecycle (ingestion design §8).
- Note the unacknowledged tension in CON-04-ext (flagged by the research digest): its vision-LLM
  financial pipeline presumes access to original PDF bytes, which lean-attachments discards. The
  deferred-class retention option above is what would make CON-04-ext-over-email possible; without
  it, financial extraction is restricted to the local-folders connector where files persist.

---

## 5. Phasing

Coverage uses the **document-bearing denominator** (~3,870 rows, §1); all-rows % in parentheses.

| Phase | Scope | New deps | Effort | Coverage after |
|---|---|---|---|---|
| **—** | today: text/* + json/xml/csv/text | — | — | ~5% (2.0% of all rows) |
| **A — text-layer formats (the 80% win on volume)** | `extraction_status` migration + `ExtractionResult` seam extension; born-digital PDF (pdfplumber→pypdf, table serialization); docx; xlsx; pptx; striprtf; size guard; skip-list statuses for images/archives/noise; dev backfill run #1 | pdfplumber, pypdf, python-docx, openpyxl, python-pptx, striprtf (all MIT/BSD) | **~2–3 days** | **~56%** (+1,959 rows → 21.3% of all rows; ~88% of PDF volume) |
| **B — TNEF recursion + sniffing** | TNEF RTF-body extraction + embedded-file recursion (tnefparse already in-tree + compressed-rtf); octet-stream magic-sniff dispatch; `application/ics` one-liner; mislabeled-legacy rescue; backfill run #2 | compressed-rtf (MIT) | **~1–2 days** | **~97%** (+~1,580 rows → ~37% of all rows; recovers 99%-of-TNEF *body text*) |
| **C — OCR for scanned** | §3 in full: detection probe, Tesseract bul+deu+eng worker image, pending_ocr queue + transient RawBlobStore, confidence gate + HiTL surface, 50-doc BG eval, backfill run #3 | pytesseract, pypdfium2, rapidocr (Apache/BSD) + apt tesseract packages (worker image) | **~2–3 days** | **~98%** (+~54 rows, 0.5% of all rows — but these are the accounting docs) |

Residue after C (~2%): ~51 legacy OLE (`unsupported_legacy_format` — LibreOffice sidecar is a
separate funded decision), encrypted (0 today), corrupt tail. Every residue row carries an explicit
status, so nothing is silently lost.

Phase order rationale: A is the volume win and unblocks the CA-CONN-04 gate for the dominant
formats; B is cheap because tnefparse is already integrated and recovers *body text* (99% of 1,560
rows); C is the only phase needing new infrastructure (worker image + queue + retention), so it goes
last — but the production byte-discard path may not ship before C *or* before pending-OCR retention
exists, whichever comes first.

---

## 6. Open decisions for Yani

1. **OCR engine sign-off:** Tesseract 5 (`bul+deu+eng`, tessdata_best) primary + RapidOCR
   second-pass — accept? Includes accepting the mandatory 50-doc BG eval before threshold freeze.
   (Deliberate divergence from CON-04's "Marker or docTR" — Marker disqualified on license.)
2. **OCR confidence threshold:** start at mean-confidence 60 with below-threshold → NULL +
   `low_confidence` + HiTL queue, calibrate from the eval — or pick a different starting bar
   (CON-04-ext used ~80)?
3. **Encrypted-PDF policy:** try-empty-password then `encrypted` + honest NULL, no password
   handling at MVP (census: 0 such files) — accept?
4. **Image OCR:** ship *nothing* (census: 0 document scans in 5,996 images), statuses only, with
   the scan-class heuristic gate held as a Phase C option per tenant corpus — accept the "don't
   build it yet" call?
5. **Deferred-class byte retention** behind RawBlobStore (beyond the transient pending-OCR class):
   retain bytes for legacy Office / audio / encrypted so later sidecars (LibreOffice, CON-03,
   passwords) can backfill without re-fetch — or strict discard + accept re-fetch as the only
   recovery? (This also decides whether CON-04-ext can ever run over email attachments.)
6. **Legacy .doc/.xls (~51 files):** accept honest NULL at MVP, with the LibreOffice-headless
   sidecar (>1 GB separate worker image, MPL-2.0) as an explicitly unfunded later item — or fund it
   now? (Cheaper middle option: verify + add `xlrd` for `.xls` only.)
7. **Seam contract change:** `extract_text` → `ExtractionResult` + the `extraction_status` /
   `extractor_name` / `extractor_version` / `ocr_confidence` columns and migration — sign off on
   the enum in §2.
8. **tnefparse risk acceptance:** LGPL-3.0 (import-only OK) + inactive maintenance — accept with
   version pin, adapter wrap, and corpus regression tests?
9. **Archive recursion:** confirmed out of scope (zip-bomb surface vs low yield) — revisit only on
   tenant demand with hard depth/size guards?
10. **Cloud/LLM OCR escalation:** confirm deferred until ZDR/EU-DPA + per-tenant opt-in + local
    engines measurably failing (§3.4), with Azure DI (EU) as the pre-vetted first candidate over
    Google DocAI (residency caveat)?
11. **Secrets in attachments:** the census found `.env` files attached as `text/plain` — their
    contents will now be extracted and indexed. Flag for a downstream secrets-detection/
    classification pass (security rule: sensitive-data classification) — track where?
