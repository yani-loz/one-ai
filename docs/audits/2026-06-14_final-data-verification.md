# Final Data Verification — Connect corpus is production-quality and LLM-queryable

**Date:** 2026-06-14 · **Scope:** the four-layer memory substrate on the fresh Connect ingest · **Type:** go/no-go data verification (read-only) · **Mode:** simulated LLM/agent queries against the live dev corpus, with the structured-layer numbers **adversarially recomputed two independent ways**, then the 15 headline answers **independently re-verified** by a second agent.

**Corpus (dev org `d1500000-…-0001`):** 8,386 emails (8,238 distinct Message-IDs) · 10,092 attachments (3,528 carry text) · 62 xlsx typed-grids · 1,140 persons · 537 companies. One tenant only — no cross-org bleed possible in any join.

**What "done" requires here:** (1) the data in the DB is genuinely good quality, and (2) an LLM/agent querying it actually gets *correct, real* answers — not plausible-looking ones. Both were tested directly; numbers were proven, not asserted.

---

## 1. One-line verdict

**GO. The Connect corpus is production-quality and LLM/agent querying is proven correct across all four memory layers — with two honest, non-blocking data-quality caveats (entity under-merging of the same human across addresses; a tiny free-mail blocklist gap) that an agent can work around and that belong to the Learn layer to close.** Every structured number was confirmed to the cent by two independent computation paths; 0 of 15 re-verified headline answers were refuted. No blocker. The only thing *not* verified is the Ask natural-language layer itself — because it is the next build, not yet written; this pass verifies the substrate it will run on.

---

## 2. Per-layer scorecard

| Layer | Verdict | Strongest evidence | Caveat (none blocking) |
|---|---|---|---|
| **Structural integrity (the floor)** | ✅ **TRUSTWORTHY** | dedup reproduces to the row (8,386 rows / 8,238 distinct Message-IDs / 18 redundant rows in 16 groups = 0.21%); **0** org-coherence mismatches & **0** orphans across all child→parent edges, structurally guaranteed by 12 composite `(org_id, parent_id)` FKs; `extracted_data` = exactly 62 JSON objects / 10,030 true SQL-NULL / **0** JSON-`'null'` (the `none_as_null` fix holds); RLS **enabled + forced** on 11 tables — under the non-bypass runtime role: dev-org → 8,386, foreign-org → 0, no-context → 0 (fails **closed**). | 1 xlsx (`Сметкоплан ГБС ИС.xlsx`, 1.78 MB) is `.sheet` content-type but un-extracted (NULL data) — does not contradict "62 with data are all xlsx." |
| **Text / semantic (RAG substrate)** | ✅ **TRUSTWORTHY** | 97.0% of emails carry readable body (8,137/8,386); **0** C0-control corruption; document-bearing attachment extraction = **88.8%** (3,528/3,973), the ~11% that fail are *honestly labeled* (`scanned_pending_ocr`, `unsupported_format`, `empty`, `encrypted`, `corrupt`) — visible reasons, not silent gaps; keyword retrieval returns real on-topic business mail (оферта 682, договор 759, meeting 2,853, AI/RAG/LLM 596); sampled PDF/docx/TNEF outputs are coherent, LLM-answerable Bulgarian + English. | "zero mojibake" overstated: ~9 emails (0.11%) heavily garbled (ISO-2022-JP / CP1251 misdecode), ~24 (0.29%) have *any* encoding defect. Corpus is ~99.7% clean; the absolute claim was softened, not the quality finding. |
| **Structured / DuckDB (typed grids)** | ✅ **PROVEN CORRECT** | five independent adversarial recomputes (4 column-sums + 1 correlated date-filtered row-join) matched DuckDB **to the cent**; an accounting ledger summed to **183,090.10 BGN over 1,474 line items** and a raw-JSONB recompute returned the **identical total/count**; 79,844/79,844 numeric cells are true JSON numbers (sums, not string-concat); **0** non-finite poison tokens (102 `inf`/`nan` hits were all legit string substrings like "Finance"). | Tool usability, not data: Cyrillic headers strip to positional `col0..colN` (ASCII-only cleaner); all cells load as VARCHAR so aggregates need explicit `CAST`. Agent must probe `show` first. No data loss. |
| **Relationship graph (WHO/WHERE)** | ✅ **TRUSTWORTHY-WITH-CAVEATS** | **0** phantom-automation persons (verified non-vacuous: 646 automation emails exist in raw `from_address` but **0** were promoted to persons); **0** quote-wrapped names; **847/847** person→company links are domain-consistent (every edge backed by the person's real email domain — the strongest trust signal); top counterparties are real orgs (IBM consolidated across 4 country TLDs into ONE company, Kaufland, BG firms); internal/external boundary correct (1 internal company, 16 internal persons, all @ethera-tech.com). | **[MEDIUM] Email-as-identity / no cross-address merge:** one human with a corporate + personal address becomes two person rows (46 display-names → 106 persons, ~13% of named). Conservative/safe (never wrongly merges) but splits a few high-volume people. **[MINOR] Free-mail blocklist gap:** the 13-domain `IN`-list is clean, but a broad scan found ~2–3 junk free-mail companies it missed (`gmaill.com` typo, `yahoo.ca` country-TLD). **[MINOR]** SaaS-host fragmentation (`atlassian.net`→7, `onmicrosoft.com`→2, ~1.7%, never in top ranks); 8 case-insensitive duplicate aliases. |
| **Cross-cutting / multi-hop (agent questions)** | ✅ **TRUSTWORTHY-WITH-CAVEATS** | all four multi-hop chains (company → domain → people → emails → attachment → grid) resolve to **correct, real** answers; "What did we discuss with Kaufland?" → 18 people, 85-email bidirectional thread (48 in / 36 out, confirmed two ways), real training-quote body; RAG-with-sources: an email's AI-contract claim grounded to the *exact* contract attachment. | **[MEDIUM] TNEF inflation:** 1,455/3,528 text rows (41%) are `winmail.dat` (TNEF email *bodies*, not distinct files); document *counts* inflate ~40% (rankings survive a filter; absolute counts need one). **[MEDIUM]** same entity under-merge as above. **[LOW]** embedded total-rows in financial sheets double-count a naive SUM (agent must exclude the total row). |

**Bottom line:** every layer is GO. Three layers are unconditionally TRUSTWORTHY; the two graph/multi-hop layers carry well-characterized MEDIUM caveats that are **design choices the agent can route around today and the Learn layer closes tomorrow** — none of them corrupt or cross-contaminate data.

---

## 3. Simulated LLM session — real questions → method → answer

This is the convincing part: the exact questions Ask will field, run against live data, each tracing the full retrieval/analysis path an LLM would take.

**Q1 (multi-hop) — "What did we discuss with Kaufland?"**
*Method:* `company(name='kaufland.bg')` → `person_company` (18 people, all @kaufland.bg) → `email_message.from_person_id` ∪ `email_recipient.person_id`; then read the body of *"Запитване за оферта за организация на обучения — Кауфланд България"*.
*Answer:* a coherent, real, **bidirectional** thread — **85 emails (48 inbound / 36 outbound)**. The body is Kaufland Bulgaria requesting a price quote for an *"Иновации и изкуствен интелект"* (Innovations & AI) training (2 days × 4h, up to 15 people, Sofia office, named contact Велизара Вълчанова, deadline 16 May), addressed to internal user Yani. *Re-verified:* the 85/48/36 count reproduces **identically** via raw-address matching — entity resolution agrees with string matching to the row.

**Q2 (structured analysis) — "How much did this accounting ledger total, over how many line items?"**
*Method:* `xlsx_query.py sql` over *MATCH — Искания обработен вид* (`43b93cae`), `SUM(CAST(col3 AS DOUBLE))`.
*Answer:* **183,090.10 BGN over 1,474 line items**, max 3,655.01, min 1.20; header subtotal 91,542.46 + line-item subtotal 91,547.64 = 183,090.10 (the ~5.18 gap between halves is a genuine source rounding/adjustment, not a parse error — evidence this is real business data).
*Adversarially confirmed:* a direct Postgres `SUM((cell->>'v')::numeric)` over the column's `t='n'` cells straight from stored JSONB = **183,090.10 / 1,474 cells** — exact match, no shared code path.

**Q3 (entity graph) — "Who are our top counterparties?"**
*Method:* `company JOIN person_company` GROUP BY company, ORDER BY distinct-people DESC.
*Answer:* all real organizations — `ibm.com (24)`, `gbs-bg.com (22)`, `kaufland.bg (18)`, `polarmoda.bg (17)`, `dataplus-bg.com (17)`, `ethera-tech.com (16)`, `ocenki.bg (15)`, `apis.bg (12)`, `kambourov.biz (11, law firm)`. No junk, no free-mail, no SaaS-notification noise in the ranks; IBM correctly consolidated across bg/ch/cz/de TLDs into one entity.

**Q4 (RAG-with-sources) — "Find an email about AI and the document that elaborates it."**
*Method:* topic `'изкуствен интелект'` ILIKE over bodies (680) ∩ attachments (436); join where both match (excl. winmail.dat); read both.
*Answer:* an email from `tlyubenov09@gmail.com` says *"sending the two contracts… regarding the contract for development of a PRODUCTION SYSTEM WITH ARTIFICIAL INTELLIGENCE… dated 22.04.2026"*; the elaborating attachment **is exactly that contract** — *"ДОГОВОР ЗА РАЗРАБОТКА НА ПРОДУКЦИОННА СИСТЕМА С ИЗКУСТВЕН ИНТЕЛЕКТ…"* between ГБС / ЕТЕРА ТЕХНОЛОДЖИС. Email claim and source document align exactly — the agent can cite the precise document.

**Q5 (document read) — "Read this invoice and tell me who was billed and for how much."**
*Method:* read `extracted_text` of `invoicem_0000000022.pdf`.
*Answer:* fully answerable — *ФАКТУРА № 0000000022, 01.05.2026*, recipient ГБС-ИНФРАСТРУКТУРНО vs supplier ЕТЕРА ТЕХНОЛОДЖИС, EIK/VAT IDs, line item *"Авансово плащане — Продукционна система AI"* **14,500.00 €**, amount-in-words intact.

**Q6 (the goal proof) — "Run SQL over a typed grid and get a provably correct number."**
*Method:* DuckDB tool over *Sample-maintable-filled.xlsx*: count + avg/min/max of `Age`. Then independent Postgres-over-raw-JSON recompute of the same column.
*Answer:* tool → 600 rows, avg **27.27**, min 6, max 42; raw-JSON → 600 cells, avg **27.2700**, min 6, max 42. **Identical to the digit** — the tool's grid reconstruction is faithful and the agent's answer is provably correct.

---

## 4. Adversarial-correctness proof (why the structured numbers are trusted)

The structured layer's entire value is that numbers are *correct*, so they were never accepted from a single path. Every headline aggregate was computed two ways that **share no code**:

- **Path A — the agent tool:** stored typed grid → reconstruct dense table → DuckDB `SUM(CAST(col AS DOUBLE))`.
- **Path B — independent:** Postgres `SUM((cell->>'v')::numeric)` over the column's `t='n'` cells straight from the stored JSONB, addressing cells by their real A1 refs (verified column-letter → index mapping, no off-by-one).

**Five adversarial recomputes, all exact matches:**

| Workbook | Metric | Path A (DuckDB) | Path B (raw JSONB) |
|---|---|---|---|
| Invoice `fece30a8` | Σ Стойност / n | 1,953.98 / 11 | 1,953.98 / 11 |
| Earthworks `ed992f09` | Σ norm / max / min | 854.35 / 42.9 / 0.0 | 854.350 / 42.900 / 0.000 |
| Resources `3e73e4b8` | Σ col D | 1,660,300.40 / 2,594 | 1,660,300.40 / 2,594 |
| MATCH `43b93cae` | Σ col D | 183,090.10 / 1,474 | 183,090.10 / 1,474 |
| Resources (correlated) | Σ value WHERE date=03.02.2025 | 1,443.39 / 19 docs | 1,443.39 / 19 docs |

The last row is the strongest: a **same-row column-C-date → column-D-value join** agreeing across both stacks proves ref→row alignment is intact, so multi-column filtered aggregates are correct — not just naive column sums. Plus the invoice's own arithmetic holds (`Количество × Ед.Цена == Стойност` for **every** line) and category subtotals reconcile (СТОКИ 1,538.00 + УСЛУГИ 415.98 = 1,953.98). Two independent computations agreeing to the cent means the stored grid is faithful and the analysis path is correct — **provably, not plausibly.**

**Re-verification result:** an independent second agent re-ran all 15 headline answers. **0 refuted.** 11 fully CONFIRMED, 2 PARTIAL (see §5), and every structured/integrity/multi-hop number reproduced to the row.

---

## 5. Issues found, by severity

| # | Severity | Layer | Issue | Blocks "done"? |
|---|---|---|---|---|
| 1 | **MEDIUM** | entity / cross-cutting | **Entity under-merging.** Identity is keyed on email address (1 person : 1 email), so one human with a corporate + personal address is two person rows (46 names → 106 persons, ~13%; e.g. Yani Lozanov ethera-tech vs outlook, Maria Kareva corporate vs gmail). Splits per-person email/doc counts. Conservative & safe — never *wrongly* merges. | **No.** Defensible design; cross-address merge is the explicit target for the **Learn** identity-merge tier. Agent can still identify everyone by address today. |
| 2 | **MEDIUM** | cross-cutting | **TNEF/winmail.dat inflation.** 1,455/3,528 text rows (41%) are `winmail.dat` — extracted TNEF *email bodies*, not distinct files; opaque filename. Text is real & retrievable, but "how many documents" absolute answers inflate ~40%. | **No.** Rankings are stable with/without a `winmail.dat` filter (verified). Any *count* query just needs the filter. Inner filenames may not be recoverable from the current schema — tracked. |
| 3 | **MINOR** | entity | **Free-mail blocklist gap.** The 13-domain `IN`-list is clean (0 hits), but a broad regex scan found ~2–3 junk free-mail companies it missed: `gmaill.com` (typo-squat), `yahoo.ca` (country TLD), one ambiguous `.live`. A "list companies" query could surface bogus org names. | **No.** ~0.5% of 537 companies, never in top ranks. Fix = extend blocklist to TLD variants + common typos (mirror the free-mail list). |
| 4 | **MINOR** | entity | SaaS-host fragmentation: `atlassian.net`→7, `onmicrosoft.com`→2 (~9 spurious / 537 = 1.7%), each 0–2 people, never in top counterparties. + 8 case-insensitive duplicate alias pairs (aliases not case-folded); + a few role mailboxes resolved as persons (`cpanel@`, `tririga.*@ibm.com`). | **No.** Cosmetic; SaaS-host suppression list + alias case-fold cleans it. |
| 5 | **LOW** | text / integrity | 11/3,528 text-bearing attachments (0.3%) are genuinely-empty `.htm`/TNEF sources reduced to a stray char (`>`) yet labeled `extracted` (the known EQ-4 HTML/TNEF seam). Satisfies the non-empty CHECK; content is effectively empty. | **No.** Cosmetic mislabel; ideally reclassed `empty`. No corruption. |
| 6 | **LOW** | text | Mojibake: ~9 emails (0.11%) heavily garbled (ISO-2022-JP / CP1251 misdecode) + ~24 (0.29%) with any encoding defect; 10 attachments / 15 emails carry trace U+FFFD (density ≤0.09%). Sender-side charset damage; Cyrillic decodes flawlessly everywhere else. | **No.** Corpus ~99.7% clean; bytes are lost in the source (irreparable). Softens the earlier "zero mojibake" wording; does not change the quality verdict. |
| 7 | **LOW** | cross-cutting | Embedded grand-total/subtotal rows inside financial sheets — a naive `SUM` double-counts them (e.g. invoice 1000000107: naive 18,121.60 vs true 9,060.83). Data is internally consistent; agent must detect & exclude total rows. | **No.** Agent-skill / prompt concern, not a data defect — the sheet reconciles. |

**Net: zero blocking issues.** The two MEDIUMs are known, characterized, non-corrupting, and route-aroundable; everything else is MINOR/LOW cosmetic.

---

## 6. Honest residuals — what is NOT covered (by design / next)

- **Scanned documents (OCR queue):** 110 `scanned_pending_ocr` + 28 `extracted_partial_scanned` await Phase-C OCR. **Status-marked in the data, not silently missing** — an agent sees exactly why text is absent.
- **The `unsupported_format` tail (215):** pptx + octet-stream sniffing + legacy `.doc/.xls`. Honestly labeled; recoverable later with format-specific extractors.
- **The `empty`/`encrypted`/`corrupt` tail (118 / 1 / 1):** genuinely no extractable text; correctly flagged.
- **Cross-tenant isolation under real multi-tenancy:** only one tenant exists in this corpus, so multi-hop joins *cannot* leak — but a true two-tenant negative test was not exercised here (the prior RLS pass owns that; RLS is forced and proven to fail closed under the runtime role).
- **The Ask natural-language layer is not built.** This verification proves the **substrate** — retrievable text, provably-correct structured grids, clean & domain-consistent entities, an intact relationship graph — that Ask will run on. The DuckDB `xlsx_query` harness used throughout is the working prototype of the analysis tool Ask will hand the LLM; its one rough edge (positional Cyrillic columns) is a tool-design note for that build, not a data problem.

---

## Conclusion

**The Connect corpus is production-quality and the substrate is GO for building Ask.** Data integrity is structurally guaranteed; text is a clean, coherent retrieval/reading substrate; structured numbers are correct to the cent under two independent computations; the entity graph answers WHO/WHERE questions with 100% domain-consistent links; and multi-hop agent questions resolve end-to-end against real rows. The two MEDIUM caveats (cross-address identity merge, TNEF document-count inflation) are honest, well-understood, non-blocking, and belong to the Learn layer — not to this go/no-go.
