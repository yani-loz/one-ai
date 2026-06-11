# 11 — IMAP data-quality pass (post-ingest graph quality)

A **second axis** of IMAP testing, sibling to `10_imap-connector/`. That pass proved *plumbing*
(never-lose-mail, crash safety, RLS tenant isolation). This one asks: **given the rows are present and
isolated, is the *derived* data any good — or did the pipeline manufacture orphans, duplicates, noise?**

The catalogue of investigation points + thresholds + severity rubric is **[`STANDARD.md`](STANDARD.md)**
(~50 points across 11 categories A–K). This README is the execution dashboard.

## Corpora (both)

| Arm | Org | What it measures | How |
|---|---|---|---|
| **LIVE** | `d1500000-…-0001` (dev) | *real* quality on a real mailbox | re-ingest of the spike dump (`yani.lozanov@ethera-tech.com`, 13.6k `.eml`) via `scripts.ingest_imap_dump` |
| **SEED** | run-stamped uuid4, marked `DQ-SEED` | how the cleaning/resolution logic mangles *known-bad* input | `harness/01_seed_adversarial.py` (40 hand-crafted messages, one per DQ point) |

> ⚠ **Concurrency hazard (learned the hard way).** Do not run a test-watcher / pytest, edit backend
> files, or run a second DB-writing harness *while* an ingest runs — pytest TRUNCATEs the graph and the
> reload/connection churn cascades the ingest into aborted-transaction failures (silent mass data loss).
> Run ingests **alone**. Read-only measurement harnesses are safe to run concurrently.

## Harnesses (`harness/`)

| Script | Role |
|---|---|
| `00_corpus_census.py` | row-count gate per org + the demo-org safety check |
| `01_seed_adversarial.py` | build + ingest the 40-message adversarial corpus into a throwaway org |
| `10_measure_graph.py` | categories **K, A, B, J** — connectivity, resolution invariants, duplication, orphans |
| `11_measure_entities.py` | categories **C, D, E, I** — noise entities, lost counterparties, classification, dead fields |
| `12_measure_content.py` | categories **F, G, H** — field/text quality, threading, attachments |
| `diag_one_email.py` | diagnostic: ingest single real emails with a full error (driver swallows it) |

Each measurement harness imports the **real** predicates (`normalize_email`, `is_role_address`,
`is_generic_email_domain`) so a check is the production rule, not a re-implementation, and auto-discovers
every org with data (LIVE + every `DQ-SEED`). Run read-only:

```
docker compose exec -T backend python - < testing/11_imap-data-quality/harness/10_measure_graph.py
```

## Status — COMPLETE

- [x] Census gate · standard authored · 3 measurement harnesses written + validated (caught/fixed the
      `references` reserved-word bug).
- [x] Full live re-ingest (13,635 stored / 0 failed) + adversarial seed (40 stored).
- [x] Final measurement across both corpora → `_evidence.txt`.
- [x] Consolidated audit → **[`docs/audits/2026-06-09_imap-data-quality.md`]
      (../../docs/audits/2026-06-09_imap-data-quality.md)**.

## Verdict (final — full corpus, 13,635 emails)

**Structurally consistent, semantically thin — and ~40% duplicated.** No corruption/regression detected
(most "floor" checks are by-construction sentinels; only E01/F04 have real teeth and pass). But the derived
data is low-information AND over-stored: **5,395 / 13,635 rows (40%) are cross-folder duplicates** of the
same logical email (the C02 raw-byte dedup doesn't collapse a message seen in multiple IMAP folders — the
single highest-leverage fix); **38% of persons have no name** (real people, not bots); **72% single-person
companies**; **77 counterparty domains lost** to role-address suppression (`office@dik.bg`); **~9 GB /
4,202 business-doc attachments with dropped text** (CA-CONN-04); automation + reply_to/sender identities
leaking in as people; `atlassian.net` fragmented into 4 companies. Dead fields confirmed
(`company_domain.source`, `person_alias`, `language` all empty). The **seed arm confirms every predicted
mechanism fires as designed**. Full severities + remediation in the audit §4/§8.

> Final numbers supersede the earlier partial-corpus figures. The full FIX_BEFORE_PROD candidates are in
> audit §8 (K04 name back-fill, D01 person/company split, C01/C02 automation gating, B03 eTLD+1, I01).
