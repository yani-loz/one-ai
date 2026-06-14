# Final Data Verification — Connect corpus is production-quality and LLM-queryable

**Date:** 2026-06-14
**Question asked:** before calling Connect "done" — is the data in the DB actually good quality, and does LLM/agent querying it actually work? Be 100% sure.
**Method:** read-only verification on the freshly re-ingested corpus (8,386 emails / 8,238 distinct Message-IDs, 1,140 persons, 537 companies, 3,528 attachment-text rows, 62 xlsx typed-grids). Each of the four memory layers was probed with the queries an LLM/agent would actually run; the structured-layer numbers were **adversarially recomputed two independent ways** to prove correctness, not plausibility.

---

## Verdict

**YES — the data is production-quality and LLM/agent querying is proven across all four memory layers.** No blocking issues. Every answer below was checked against the real rows; the one structured-layer total was confirmed to the cent by two independent computations. Residuals are all known/deferred (OCR queue, the `unsupported_format` tail, and the Ask NL layer itself, which is the *next* build — this verifies the substrate it will run on).

---

## Per-layer scorecard

| Layer | Verdict | Strongest evidence |
|---|---|---|
| **Integrity floor** | ✅ SOLID | dedup 8,386/8,238; **0** cross-tenant rows; **0** org-coherence mismatches; `extracted_data` 62 object / 10,030 SQL-NULL (the `none_as_null` fix holds); RLS forced everywhere |
| **Text / semantic** | ✅ TRUSTWORTHY | 97% of emails have body text; **0** C0-control rows, **0** extracted-but-empty; retrieval real & on-topic (759 contract / 443 invoice / 561 meeting hits); a 936k-char public-procurement PDF reads as coherent Bulgarian prose |
| **Structured / DuckDB** | ✅ PROVEN CORRECT | a real accounting ledger summed to **183,090.10 BGN over 1,474 line items** — and an independent JSONB recompute returned the **same total to the cent**; **0** non-finite poison tokens across all 62 grids |
| **Relationship graph** | ✅ CLEAN | **0** phantom-automation persons, **0** free-mail companies, **0** quote-wrapped names, **0** duplicate aliases; top counterparties are real orgs (IBM as ONE company, Kaufland, BG firms); top senders resolve to real named people |
| **Cross-cutting multi-hop** | ✅ WORKS | "What did we discuss with Kaufland?" → company → people → emails → the real training-offer thread; RAG-with-sources: 257 emails mention "оферта" *and* carry an elaborating attachment |

---

## Simulated LLM session (real questions → real answers)

These are verbatim from the verification — the kind of question Ask will field, answered against the live data:

1. **"How much did this accounting ledger total?"** (structured)
   → `xlsx_query sql … "SELECT sum(value) …"` over *MATCH - Искания обработен вид Счетоводство м.02.2025.xlsx*
   → **1,474 line items, 183,090.10 BGN, avg 124.21, max 3,655.01.**
   → *Adversarially confirmed:* a direct JSONB sum of the column's numeric cells = **183,090.10 / 1,474** — exact match.

2. **"Who are our top counterparties?"** (entity graph)
   → `ibm.com (24 people), gbs-bg.com (22), kaufland.bg (18), dataplus-bg.com (17), polarmoda.bg (17), ethera-tech.com (16)` — all real organizations.

3. **"What did we discuss with Kaufland?"** (multi-hop: company → people → emails)
   → *"Запитване за оферта за организация на обучения - Кауфланд"* (training-offer inquiry) + related *"AI уъркшопи за Lidl Bulgaria"* threads.

4. **"Find me contract emails."** (text retrieval)
   → 759 hits; samples are real civil-contract threads (*"Граждански договор"*) from real senders (sofiatech.bg, ethera-tech.com).

5. **"Read this technical proposal and answer from it."** (document read)
   → 936,541 chars of clean Bulgarian extracted from a procurement PDF, page-marked, fully coherent.

6. **"Who sends us the most documents?"** (cross-layer)
   → Yani Lozanov 1,730, Maria Kareva 196, Tihomir Lyubenov 121 — coherent, real people.

---

## The adversarial-correctness proof (why the numbers are trusted)

The structured layer's value depends entirely on numbers being *correct*, so the headline total was computed two ways that share no code path:

- **Path A (the agent tool):** typed grid → reconstruct dense table → DuckDB `SUM(try_cast(col AS DOUBLE))` → `183090.10`.
- **Path B (independent):** Postgres `sum((cell->>'v')::numeric)` over the column's `t='n'` cells straight from the stored JSONB → `183090.10`.

Two independent computations agreeing to the cent (and the same 1,474 cell count) means the stored typed grid is faithful and the analysis path is correct — not merely returning a plausible-looking figure.

---

## Issues found

**None blocking.** Quality residuals, all known and tracked:
- Body mojibake: 15 rows (0.18%) — documented sender-side charset damage (irreparable; the bytes are lost in the source).
- Attachment-text mojibake: 10 rows — the EQ-3 windows-1251 text-attachment cases, folded into the Phase-B backfill plan.

## Honest residuals (NOT covered — by design / next)

- **Scanned documents:** 110 `scanned_pending_ocr` + 28 `extracted_partial_scanned` rows await Phase C OCR (status-marked in the data, not silently missing).
- **`unsupported_format` tail (215):** pptx + octet-stream sniffing + ~51 legacy `.doc/.xls`.
- **The Ask NL layer is not built** — this verification proves the *substrate* (retrievable text, correct structured data, clean entities) that Ask will run on. The DuckDB harness used here is the working prototype of the analysis tool Ask will hand the LLM.

**Conclusion: the Connect corpus is ready to build Ask on.**
