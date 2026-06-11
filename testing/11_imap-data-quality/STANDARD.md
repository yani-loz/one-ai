# Data-Quality Points of Investigation — IMAP ingest → entity graph

**Status:** STANDARD (investigation catalog) — **executed twice**: 2026-06-09 (first pass →
`docs/audits/2026-06-09_imap-data-quality.md`) and 2026-06-10 (full-DB re-audit →
`docs/audits/2026-06-10_db-data-quality-audit.md`). Current corpus numbers live in the
**Baselines** section at the end of this file — the next pass grades against those, not against the
catalog rows' first-run figures.

**Scope.** A *different axis* from the `10_imap-connector` pass. That pass proved **plumbing**:
never-lose-mail, crash safety, DB-level tenant isolation (RLS holds). This standard asks the opposite
question — given the rows are present and isolated, **is the *derived* data any good, or did the pipeline
manufacture orphans, duplicates, and noise?** The consumer downstream is cross-source RAG + an entity
dossier + the compounding-intelligence loop, so a noisy graph silently degrades every answer.

**Source of these points.** Author's adversarial enumeration + a cross-vendor advisory pass (GPT-5.5,
`/gpt-advisor`, 2026-06-09) — every code claim re-verified against source before inclusion. Provenance is
tagged per point (see legend).

---

## OPEN DECISION (pick before execution)

Two corpora test different things; the standard is written to run against **either or both**:

1. **Live measurement** — run the metrics read-only against the **already-ingested dev-org graph**
   (`org_id` `d1500000-…`, per `connect-ingest-dev-org`). Measures *real* quality on *real* mail.
   Needed for the lifecycle/orphan points (J*). ⚠ Do **not** run the backend pytest suite during a
   measurement window — it wipes the entity graph (known gotcha).
2. **Adversarial seed** — ingest a **run-stamped throwaway org** (uuid4) loaded with deliberately-nasty
   messages (RFC2047 umlaut names, gmail-dot/+alias duplicates, `kontakt@kunde.de`, newsletter
   subdomains, punycode domains, latin-1 CSV attachments, empty-plain-rich-html, colliding Message-IDs,
   Bcc-only delivery). Measures whether the *cleaning/resolution logic* mangles known-bad input.

Most points run in both. Points marked **[live]** need the real corpus; **[seed]** are best stress-tested.

---

## Severity rubric (DQ-specific)

| Severity | Trigger |
|---|---|
| **Critical** | Wrong entity linkage; internal/external inversion; irreversible content loss; anything that poisons top dossiers / RAG answers. |
| **High** | Affects top entities or >1–5% of messages/entities; systematic recall/precision loss. |
| **Medium** | Localized, recoverable, or mostly long-tail entities. |
| **Low** | Cosmetic, rare, or already quarantined from retrieval. |

A defect's severity is **conditioned on reach**: the same flaw is Critical if it hits a top-20 dossier
entity and Low if it only touches a one-off long-tail address. Every metric therefore reports **both** a
rate and a *top-entity* slice (rank entities by message/thread degree, inspect the head).

## Provenance / result legend

`🆕 NEW` first surfaced here · `📋 DOCUMENTED` already tracked (FIX_BEFORE_PROD / design) · `✔ FIXED`
confirms a prior fix holds · `🔬 INVARIANT` a must-hold property (any breach = bug). Per-point **Source**:
`A` author, `G` GPT advisory, `A+G` both independently. Result column filled at execution:
✅ clean · ⚠ concern · ❌ defect.

---

## A — Entity-resolution integrity (must-hold invariants)

| ID | Investigation point | Metric (SQL-able against the graph) | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-A01 | A source row's `person_id` must own the **normalized as-seen** address. `email_message.from_person_id` / `email_recipient.person_id` should always have a `person_email` row with `email = normalize(address)`. | For every non-null link, assert ∃ `person_email(person_id, email=lower(trim(strip<>(address))))`. Count violations. | `0` | Critical | 🔬 INVARIANT · A+G |
| DQ-A02 | Identity minted from a **truncated** address (sanitize caps at 320 / msg-id 998 before resolve). | Count resolved `from_address`/`person_email.email` with `length=320`; `message_id` with `length=998`. | `0` resolved | Critical (addr) / High (id) | 🆕 NEW · G |
| DQ-A03 | Every `person_email.email` is already its own normal form (idempotent key). | Count rows where `email <> lower(trim(...))`. | `0` | High | 🔬 INVARIANT · A |
| DQ-A04 | No person without ≥1 `person_email`; no `person_email` orphaned from its person. | Anti-join both directions. | `0` | High | 🔬 INVARIANT · A |

## B — Duplication / under-merge (one real entity → many rows)

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-B01 | **Gmail-dot / +subaddress duplicate persons** — `j.smith@gmail`, `jsmith@gmail`, `j.smith+x@gmail` resolve to 3 persons (normalizer is dot/plus-naive **by design**). | Group `person_email` on `(replace(localpart,'.',''), domain)` for generic domains, also collapse `+suffix`; count groups with >1 person. | low; documented | Medium | 📋 by-design · A |
| DQ-B02 | **Same human, work+personal** — no name-based merge (v1 deterministic-only). | Persons sharing an identical non-empty `display_name` but disjoint domains; rank by degree. | review head | Medium/High | 🆕 NEW · A+G |
| DQ-B03 | **Company fragmentation by subdomain** — `mail.acme.com` vs `acme.com` → 2 companies (no eTLD+1). | Group `company_domain` by registrable suffix heuristic; count companies sharing a parent. | low | High | 🆕 NEW · A+G |
| DQ-B04 | **Company fragmentation by IDNA/punycode** — `münchen.de` vs `xn--mnchen-3ya.de`. | Domains with `xn--`; unicode domains whose punycode twin also exists. | `0` pairs | High | 🆕 NEW · G |
| DQ-B05 | **Duplicate *messages*** — dedup = `sha256(raw_bytes)`, so the same logical email re-fetched with one byte changed (extra `Received`, CRLF) → a 2nd row. (NB: `email.py` docstring still says "Message-ID else hash" — **stale vs code**, which uses raw-byte hash always.) | Same `(message_id, from_address, subject, date_trunc('minute',sent_at))` across distinct `dedup_key`. | <0.1% | Medium/High | 🆕 NEW · A |
| DQ-B06 | **Duplicate recipient edges** — parser appends every occurrence; no per-message dedup of `(email_id, kind, address)`. Inflates relationship strength. | Count duplicate `(email_id, kind, lower(address))`; same address across multiple kinds. | <0.1% msgs | Medium/High | 🆕 NEW · G |

## C — Noise / spurious entities (should not exist)

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-C01 | **Automated/list senders mint people** — `is_automated` is computed (`flags.py`) but the resolver **never consults it**; only `is_role_address` gates person-hood. A newsletter from `updates@news.brand.com` (List-Unsubscribe) → person + company. | `email_message.is_automated=true AND from_person_id IS NOT NULL`; persons whose *only* evidence is automated mail. | <1% persons; no top entities | High | 🆕 NEW · A+G |
| DQ-C02 | **reply_to / RFC `Sender` identities become people** — `_extract_recipients` stores both kinds; `_store_recipients` resolves them. These are often routing/service identities. | Persons referenced *only* by `email_recipient.kind IN ('reply_to','sender')`, never from/to/cc. | ~0 high-degree | High | 🆕 NEW · G |
| DQ-C03 | **Bogus companies from typo/garbage domains** that miss the freemail filter (`gmial.com`, `acme.con`). | `company_domain.domain` failing a public-suffix/MX-plausibility check; single-message companies on look-alike domains. | low | Medium | 🆕 NEW · A |
| DQ-C04 | **IP-literal / single-label / malformed-domain companies** — `extract_domain` takes raw text after last `@`, no validation. | Domains that are `[ip]`, single-label, contain `_`, or trailing dot. | <0.1% | High (if internal/top) | 🆕 NEW · G |
| DQ-C05 | **Role-mailbox noise that *should* persist as a company** (see D01) vs genuinely-junk role mail — separate the two. | Cross-ref C with D01 candidates. | n/a | n/a | A+G |

## D — Lost signal / under-creation (should exist but doesn't)

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-D01 | **Role address suppresses the COMPANY too** — `resolve_participant` returns `None` for a role local-part *before* company resolution, so `kontakt@kunde.de` mints neither person **nor** the `kunde.de` company. For DACH SMB (lots of `info@`/`kontakt@`/`buchhaltung@`) this is likely the **highest-impact** DQ defect. | Distinct non-generic domains appearing **only** in role/excluded addresses and **absent** from `company_domain`; rank by message/thread volume. | no high-volume domain missing | High/Critical | 🆕 NEW · G |
| DQ-D02 | **Generic-domain tenant** — if the connection's own mailbox is on `gmx.de`/`web.de`, the tenant's own company never forms and colleagues aren't internal (only the exact mailbox address is). Real for DACH micro-SMB. | Connection username domain ∈ generic set; same-domain high-volume contacts with no company. | tenant not on generic domain | Critical (if so) | 🆕 NEW · G |
| DQ-D03 | **High-volume real counterparties with no company** (any cause). | Non-generic sender/recipient domains by message count, absent from `company_domain`. | head explained | High | A+G |
| DQ-D04 | **Mailbox is not a participant** — Bcc/alias inbound delivery leaves the mailbox in neither From nor any recipient; evidence only in `Delivered-To`/`X-Original-To`/`Envelope-To` (JSONB headers). | Inbound msgs where mailbox ∉ {from_address} ∪ recipients. | bounded | Medium/High | 🆕 NEW · G |

## E — Classification correctness (flags that lie)

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-E01 | **is_internal drift / inversion** — `_is_internal` is a **create-time stamp**, never repaired; in a multi-connection org a shared person is stamped by whichever connection saw them first. | Own-domain persons with `is_internal=false`; non-own-domain with `is_internal=true`; same for companies. | `0` | Critical (inversion) / High | 🆕 NEW · G |
| DQ-E02 | **direction is mailbox-centric, not tenant-centric** — `derive_direction` compares From only to the single connection username. An internal colleague's mail → `inbound`. | `direction='inbound' AND from_person.is_internal=true`; alias senders classed inbound. | documented | High | 🆕 NEW · G |
| DQ-E03 | **is_automated false-negative/positive** — header signals (List-*, Precedence, Auto-Submitted) + a *small* localpart set; bulk mail without those headers slips through. | Senders with marketing markers in `headers` but `is_automated=false`; vice versa. | low both | Medium | 🆕 NEW · A |
| DQ-E04 | **is_reply correctness** — `Re:/Aw:/Antw:/Sv:` matched, `Ref:` excluded (business ref). Check both error directions. | `is_reply=true` with no `in_reply_to` and a non-reply subject; `is_reply=false` with `In-Reply-To` present. | low | Low | A |

## F — Field / text quality (the stored content)

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-F01 | **Dirty `display_name`** — set once at first sighting (`_get_or_create_person`), never improved; could be an RFC2047-undecoded word, control chars, the address itself, empty/whitespace, or `"Undisclosed recipients"` / a contact-book nickname (`Chef`, `Buchhaltung`). | display_name matching `=?...?=`, control chars, `==address`, blank, or known junk; conflicting-name count per person. | <5% contested; no top people | High | 🆕 NEW · A+G |
| DQ-F02 | **Body mojibake** — `_decode_text_part` uses the part charset w/ `errors='replace'`; double-encoding still yields `Ã¤/Ã¶/Ã¼/ÃŸ` or `�`. DACH-critical. | `body_text` containing `�` or `Ã[¤¶¼Ÿ]` patterns; rate per row + per char. | <0.1% rows | High | 🆕 NEW · G |
| DQ-F03 | **Plain-part-wins hides rich HTML** — `_extract_body_text` prefers `text/plain`; an empty/placeholder plain part hides the real HTML body → RAG recall loss. | `word_count < 5 AND size_bytes > 50KB` AND headers show multipart/alternative or text/html. | <1% msgs | High | 🆕 NEW · G |
| DQ-F04 | **Degraded parse rows are semantically empty** — `parse_status='failed'` ⇒ no from/subject/body (stored, not dropped — correct, but blank). | `parse_status='failed'` rate; slice by recency + `size_bytes`. | <0.1% | High (Critical if top/legal threads blank) | 📋 DOCUMENTED (C01) · A+G |
| DQ-F05 | **word_count integrity** — `len(body_text.split())`; html-flatten markdown tokens inflate it. | `word_count` vs recomputed from `body_text`; outliers. | exact | Low | A |
| DQ-F06 | **Date sanity** — `sent_at` (Date header, attacker-set) vs `received_at` (INTERNALDATE/Received). | `sent_at` epoch/far-future; `sent_at` ≫ `received_at`; both null. | low | Medium | 🆕 NEW · A |
| DQ-F07 | **Subject quality** — RFC2047 leftovers / control chars / empty. | `subject` matching `=?...?=` or control chars. | <0.1% | Low/Medium | A |

## G — Threading integrity

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-G01 | **Message-ID collision over-merges threads** — `message_id` is stored, **not unique**; two distinct emails (forward, reuse, attacker) share an id. Dedup keys on raw bytes so both are stored, but thread-joins on `message_id` mis-merge. | Same `message_id` with differing `dedup_key/from/subject/sent_at`. | `0` conflicting | Critical (if threads feed dossiers) | 🆕 NEW · G |
| DQ-G02 | **Dangling references** — `references[]`/`in_reply_to` point at ids absent from the corpus → fragmented threads. | refs/in_reply_to with no matching `message_id` row; rate. | bounded by corpus completeness | Medium/High | 🆕 NEW · G |
| DQ-G03 | **Ambiguous in_reply_to** — matches multiple rows. | `in_reply_to` joining >1 `message_id`. | low | Medium | G |

## H — Attachment content

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-H01 | **Binary (PDF/Office) text lost** — `extract_text` returns `None` for non-text; `_store_attachments` drops the bytes. In the current path the document text is **gone permanently**. | `email_attachment` with business-doc `content_type` and `extracted_text IS NULL`; volume of bytes dropped. | prod: 0 irreversible | Critical (prod) | 📋 DOCUMENTED (CA-CONN-04) · A+G |
| DQ-H02 | **Attachment decode is utf-8-only** — `_decode_text` hardcodes `utf-8`, ignoring the part charset (unlike the body). Latin-1/cp1252 CSV/text (common DACH exports) → mojibake. | text/csv/text attachments with `�`/mojibake in `extracted_text`. | <0.1% | High | 🆕 NEW · G |
| DQ-H03 | **Attachment metadata sanity** — filename RFC2047-undecoded, `size_bytes=0`, missing content_type. | filename `=?...?=`; `size_bytes=0` with non-null hash; null content_type rate. | low | Low | A |

## I — Provenance / metadata hygiene / dead fields

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-I01 | **`company_domain.source` never set** — resolver builds `CompanyDomain` without `source` (vs `person_email.source`, which it sets). | `company_domain.source IS NULL` rate. | 0 or documented-dead | Medium | 🆕 NEW · G |
| DQ-I02 | **`person_alias` never written** — the resolver captures no aliases, so name disambiguation has no fuel. | `count(person_alias)` vs persons with multiple observed names. | documented-deferred | Low | 🆕 NEW · A |
| DQ-I03 | **`language` always NULL** — never populated by the parser. | `language IS NOT NULL` rate. | documented-dead | Low | A |
| DQ-I04 | **Headers JSONB uncurated** — `build_headers` stores every header, original-case keys; bloat, case-dup keys, internal routing/security headers, raw RFC2047 leftovers. Risk if the blob ever feeds retrieval. | `pg_column_size(headers)` distribution; case-insensitive dup keys; presence of `DKIM/Authentication-Results/X-*`. | no sensitive headers in any RAG index | High (if indexed) | 🆕 NEW · G |

## J — Graph lifecycle / orphans  **[live]**

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-J01 | **Orphan persons** — person with **no** message reference (`from_person_id`/`recipient.person_id`), e.g. after a connection/message delete (FKs `SET NULL`) — the graph is **never GC'd**. | persons with zero inbound references. | documented-by-design | Medium | 🆕 NEW · A+G |
| DQ-J02 | **Orphan companies** — company with **no** `person_company` link (post-deletion; no GC). | companies with zero links. | documented | Medium | 🆕 NEW · A |
| DQ-J03 | **Orphan company sub-rows** — company with no `company_domain`; `person_company` pointing at a deleted person/company. | anti-joins. | `0` (FK should prevent) | Low | A |

## K — Graph connectivity / orphan-node lens  **[live]** (the question you led with)

The classic "is this graph noisy?" view: not strict FK-orphans (category J) but **junk / disconnected
nodes**. These are the most decision-useful headline numbers in live mode and map 1:1 to "orphaned
persons, companies, noise."

| ID | Investigation point | Metric | Green | Severity | Prov / Src |
|---|---|---|---|---|---|
| DQ-K01 | **Person on a non-generic domain with zero `person_company` link.** Near-invariant: `resolve_participant` returns `None` for role/empty addresses, so every *person* is non-role; `_resolve_company` skips linking **only** for generic domains. So a person whose email is on a non-generic domain **must** have a company link — any with zero is a genuine orphan/bug, not by-design. | persons whose every `person_email.domain` is non-generic, with `0` rows in `person_company`. | `0` | High | 🔬 near-INVARIANT · A(adv) |
| DQ-K02 | **Decompose `email_recipient.person_id IS NULL`** into three buckets — (a) role-excluded, (b) empty/garbage address, (c) **plausibly-real-but-unresolved**. Bucket (c) is the direct "we dropped a real person" signal. | classify each null-person recipient via `is_role_address` / `@`-validity; report the 3 rates. | (c) ≈ 0 | High | 🆕 NEW · A(adv) |
| DQ-K03 | **Degree-1 singletons** — persons appearing in exactly one message; companies with exactly one person/message. Degree distribution is *the* entity-graph quality lens; a fat degree-1 mass = noise. | degree histogram for person + company; share at degree 1; inspect a sample of the head. | reasonable tail, no junk head | Medium | 🆕 NEW · A(adv) |
| DQ-K04 | **Blank / NULL `display_name` persons** — the most literal "junk person." Headline count, not buried in F01. | `display_name IS NULL OR trim(display_name)=''` count + share; cross with degree. | low; none in top-degree | Medium/High | 🆕 NEW · A(adv) |

---

## Investigate-first set (the ones that matter most for RAG + dossiers + DACH)

1. **DQ-K01 / DQ-K03 / DQ-K04** the connectivity headline — orphan persons, degree-1 noise mass, blank
   display-names (this is the "is the graph noisy?" answer you led with; mostly pure-SQL, fast).
2. **DQ-D01** role-address suppresses the counterparty company (DACH `info@`/`kontakt@`).
3. **DQ-E01 / DQ-E02** internal/external drift + mailbox-centric direction (inversions poison dossiers).
4. **DQ-A01 (+A02)** source-row ↔ person-email invariant, incl. truncation.
5. **DQ-C01 / DQ-K02** automated/list senders entering the graph; dropped-real-person bucket.
6. **DQ-H01 (+H02)** attachment text loss / mojibake for business docs.
7. **DQ-G01** Message-ID collision / thread integrity.
8. **DQ-F01 / DQ-F02 / DQ-H02** display-name contamination + DACH encoding mojibake (umlauts, latin-1).

## Candidate design fix (noted, NOT actioned here)

GPT's structural suggestion, which I endorse: **split "should this become a Person?" from "should we
observe this Company/domain?"** Today a role address kills both (DQ-D01). `kontakt@kunde.de` should not
mint a person but *is* strong evidence `kunde.de` is a real counterparty. This is the single change that
removes the highest-impact DQ defect — but it is a code change, deferred until after this standard is
executed and the defect is measured.

## Method per point (so the executor knows the tool)

- **Pure-SQL** (run straight against the graph, no Python predicates): A01·A03·A04, B02·B03·B04·B05·B06,
  C02·C03·C04, D03·D04, E01·E02·E04, F03·F04·F05·F06·F07, G01·G02·G03, H01·H03, I01·I02·I03·I04,
  J01·J02·J03, **K01·K03·K04**.
- **Needs a harness** (must call the *actual* Python so the test is faithful, not a re-implementation):
  D01·C01·E03·K02 (call `is_role_address`/`is_automated`), A01/A02 normalization round-trip
  (`normalize_email`), B01 dot/plus collapse, F01·F02·H02 mojibake/RFC2047 regex (SQL-able but a harness
  is cleaner for the DACH patterns).
- A few are **seed-only-meaningful** if the live corpus lacks the input (B04 punycode, F03 empty-plain,
  G01 collisions, H02 latin-1) — note in the TC when live data is too thin to conclude.

## Execution protocol (when we run it)

- **First live step is a row-count of the dev-org graph** (`person`/`company`/`email_message`/
  `email_recipient`/`email_attachment`). If the spike corpus is small, **rates and top-entity slices are
  not meaningful** — `[seed]` mode becomes primary and live numbers are reported as raw counts only.
- **Read-only** on the dev-org real corpus; **run-stamped throwaway orgs (uuid4)** for any seeding;
  clean up only own rows. **Never** touch demo orgs `…0001`/`…0002` or `super@ethera.ai`.
- Harnesses piped over stdin into the backend container (`docker compose exec -T backend python - < …`),
  same as the `10_imap-connector` pass. No writes under `backend/`/`frontend/` during a run.
- ⚠ Do not run the pytest suite during a **[live]** measurement — it wipes the entity graph.
- Each point → a `TC-DQ-<ID>.md` with: hypothesis, query, raw result, rate + top-entity slice, verdict
  (✅/⚠/❌), tag. Consolidate into `docs/audits/<date>_imap-data-quality.md`.

## Reconciliation with the `10_imap-connector` pass — prior **C02**

The prior pass listed **C02** ("dedup poisoning: a colliding Message-ID lets a decoy suppress a genuinely
different email → silent skip") as a *pending fix*. Re-reading current source, `email_parser.py` keys
`dedup_key` on **`sha256(raw_bytes)` always** (never the Message-ID) — and its docstring argues *exactly*
this anti-poisoning rationale. **So C02-as-described appears already mitigated by design**: a colliding
Message-ID with different bytes yields a different hash → it is stored, not skipped. The remaining dedup
concern **inverts** to over-storage of near-duplicates (**DQ-B05**). (Note the stale `email.py` model
docstring still says "Message-ID, else hash" — a doc/code mismatch worth correcting.) **Action: confirm
C02 is closed before spending effort "fixing" it; redirect that effort to B05 if anywhere.**

---

## Baselines — 2026-06-10 full-DB audit (grade the next pass against these)

Authoritative source: `docs/audits/2026-06-10_db-data-quality-audit.md` §5 (right-hand column).
Corpus: dev org `d1500000-…`, 13,635 `.eml` on disk → **13,583 stored** (52 dedup-skipped, 0 failed).
Deltas that exactly equal a landed remediation (person −73 = DQ-C02 gating, company +77 = DQ-D01,
person_alias 0→1,293 = DQ-K04) are **expected movement, not regressions** — see audit §3 (B-17).

| Metric (DQ point) | Baseline (2026-06-10) | Reading |
|---|---|---|
| Duplicate message groups / redundant rows (DQ-B05) | 2,537 groups / 5,343 redundant rows = **39.3%** of email_message | ❌ vs <0.1% green — the content-key remediation removed only the 52 byte-identical copies. **Fix landed 2026-06-11** (content-identity `_dedup_key`): **VERIFIED 2026-06-11 (key v3: + UTC-instant Date + TNEF presence marker): 8,386 stored / 5,249 skipped; content-identical residual = 18 rows (0.21%), the NEW baseline** — see the audit report §7. |
| Ingest dedup skips | 52 / 13,635 (0.4%) | pre-fix figure (byte-identical copies only) |
| person rows | 1,154 | −73 vs 06-09 = DQ-C02 gating ✓ |
| company rows | 572 | +77 vs 06-09 = DQ-D01 role-suppressed domains observed ✓ |
| person_alias rows | 1,293 | DQ-K04 alias writing landed ✓ (was a dead table) |
| NULL display_name (DQ-K04) | 332/1,154 = 28.8% | improved from 38%, but the top-degree head is still nameless — red on the "none in top-degree" axis |
| Persons / companies per 1,000 emails | 84 / 42 | expected drift from the two remediations |
| Subdomain company pairs (DQ-B03) | 37 | **tripled** vs 12; eTLD+1 folding still missing |
| Within-message dup recipient edges (DQ-B06) | 183 groups / 199 redundant rows | unfixed vs 06-09 |
| Attachment extracted_text mojibake (DQ-H02) | 9 | unchanged, documented limitation |
| Address-as-display-name persons (DQ-F01 bucket) | 15 | grown from 8; still inside the <5% budget |
| Body mojibake (DQ-F02) | 41 rows = 0.30% | 3x over the <0.1% green (first measurement) |
| NULL-direction draft rows | 80 | by-design (unsent drafts — audit §3 B-6) |
| Attachment content-type census | 9,109 png / 2,355 tnef / 2,306 pdf / 1,641 docx | byte-identical across audits — deterministic parse ✓ |
| Punycode/IDN pairs (DQ-B04) | 1 pair (breeze.no Cyrillic-о homoglyph) | target 0; quarantine mechanism needed |
| Business-doc census, extraction 0% (DQ-H01 / CA-CONN-04) | 2,306 PDF / 1,641 docx / 99 xlsx / 68 pptx / 61 doc | full corpus (~8x the 1,500-email sample baseline) |

### New-in-this-audit baselines (first measured 2026-06-10 — no prior figure)

| Metric | Baseline | Maps to |
|---|---|---|
| Quote-wrapped names (Outlook `'Name'` convention) | 9,215 recipient rows (33%) / 101 persons (8.8%) / 398 aliases (30.8%), of which 347 have an unquoted twin | audit H-3 (DQ-F01 axis) |
| Phantom automation persons (compound no-reply localparts) | 13 persons (worst: a 10-alias over-merge hub) | audit H-4 (DQ-C01 blind spot) |
| Free-mail-domain companies | abv.bg (22 people, rank #1) + mail.bg (1) | audit M-7 (DQ-C03/DQ-K03) |
| C0 control chars surviving into body_text | 19 rows (U+0007/U+000B) | audit L-6 (DQ-F02 axis) |
| Non-addr-spec from_address | 9 rows (`'System Administrator'` Exchange NDRs, stored `is_automated=false`) | audit L-7 |
| org-FK coverage (org_id tables with a FK to organizations) | 1/15 (users only) — the phantom-tenant enabler | audit H-2 |
| audit_log connector lifecycle coverage | 0 `connector.*`/`sync.*` actions (fix in flight — see FIX_BEFORE_PROD CA-CONN-06) | audit H-5 |
