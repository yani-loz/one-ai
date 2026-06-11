# IMAP data-quality audit — post-ingest entity-graph quality

**Date:** 2026-06-09 · **Scope:** IMAP connector → person/company entity graph · **Type:** data-quality
(a second axis after the `2026-06-09_imap-connector-dynamic-adversarial` plumbing pass) · **Mode:**
read-only measurement over two corpora · **Standard:** [`testing/11_imap-data-quality/STANDARD.md`]
(../../testing/11_imap-data-quality/STANDARD.md) (50 points, 11 categories A–K).

> Privacy note: the LIVE corpus is the auditor's **own** mailbox. External personal local-parts are
> masked here (`x***@domain`); domains and role/no-reply addresses are kept because they are the
> analytically relevant unit and carry no personal PII.

---

## 1. Headline verdict

**The graph is structurally sound but semantically thin.** The ingest never corrupts, orphans, or
mis-links — but it enriches poorly. Across **13,635 real emails** (1,227 persons, 495 companies, 17,386
attachments):

- ✅ **No corruption or regression detected** — but read §3 for the important caveat: most of the "floor"
  checks (A01/A03/A04/K01/J01/J02) are **by-construction consistency sentinels** that are *expected* to be
  0 given today's single-write resolver path, so 0 is a clean **regression guard for the future
  merge/HITL tier**, not independent semantic proof. The two floor checks that have a *demonstrated
  negative control* — **E01** (internal/external; fired in seed) and **F04** (degraded parse; fired in
  seed) — also pass on LIVE. Tenant-isolation proof proper belongs to the prior RLS pass, which had teeth.
- ❌ **The derived data is low-information AND ~40% duplicated.** **5,395 of 13,635 rows (39.6%) are
  redundant cross-folder copies** of the same logical email (8,240 unique Message-IDs); **38% of persons
  have no name** (real humans, not bots); **72% of companies are single-person**; **77 counterparty
  domains are missing** (role-address suppression); **~9 GB of document text is dropped** (4,202 PDF/Office
  attachments, themselves duplicated); automation / routing identities leak in as people.

For an enterprise RAG + dossier product: **no data-of-record loss or cross-tenant defect surfaced** (those
were the plumbing pass's domain), and no corruption/regression was detected — but the *measured*
enrichment is poor. The honest one-liner: **structurally consistent, semantically thin.**

The **seed arm independently confirms every predicted defect mechanism fires as designed** (40 crafted
messages), so the LIVE rates are the real-world magnitude of known, explainable behaviours — not
mysteries.

---

## 2. Method

| Arm | Org | Corpus | Result |
|---|---|---|---|
| **LIVE** | `d1500000-…-0001` | full re-ingest of the spike dump — 13,635 `.eml`, `yani.lozanov@ethera-tech.com` | stored=13,635 skipped=0 failed=0 |
| **SEED** | `ee109f77-…` (run-stamped, `DQ-SEED`) | 40 hand-crafted adversarial messages, ≥1 per DQ point | stored=40, incl. 1 degraded-parse |

Three read-only harnesses (`testing/11_imap-data-quality/harness/10–12_measure_*.py`) compute every
metric for both orgs, importing the **real** predicates (`normalize_email`, `is_role_address`,
`is_generic_email_domain`) so each check is the production rule, not a re-implementation. Raw output:
`testing/11_imap-data-quality/_evidence.txt`.

**Clean slate confirmed:** the full ingest reported `skipped=0` — a re-ingest into an org still holding
rows from the earlier partial/killed runs would have shown dedup-skips, so the LIVE org is exactly the
13,635-email corpus and nothing else (no contamination from the aborted attempts).

**Process note (recorded as a real hazard):** the first attempts saw catastrophic mass ingest-failure +
vanishing committed rows. Root cause was **not** a pipeline bug — a concurrent test-watcher was
TRUNCATEing the graph mid-ingest (the documented "pytest wipes the graph" gotcha) and cascading the
ingest into aborted transactions. Run in isolation, the ingest stored all 13,635 cleanly. Ingests must
run alone; read-only measurement may run concurrently.

---

## 3. What HOLDS — and the honest strength of each ✅

Two tiers. **Tier B sentinels can only fail under manual DB corruption or a future dual-write/merge
path** — so their 0 is a *regression guard*, not a discrimination result (the large denominator is
coverage, not teeth). Tier A checks have a **demonstrated negative control** (they fired in the seed),
so their LIVE-pass is real evidence.

**Tier A — checks with a demonstrated negative control (real evidence):**

| Point | Check | Negative control | LIVE result |
|---|---|---|---|
| **DQ-E01** | internal/external inversion | **fired in seed** (gmx tenant → 1 external-but-internal) | **0** |
| **DQ-F04** | failed/degraded parse stored, not dropped | **fired in seed** (forced RecursionError → 1 `failed`) | **0** failed on 13,635 |
| **DQ-F06/F07** | epoch/future dates · subject control chars | seed forced epoch/future/skew + a BEL subject | **0** (80 legitimately date-less) |
| **DQ-E02 direction** | mailbox-centric direction computed correctly | spot-checked | 8,308 outbound, **all** `from_address == mailbox` |

**Tier B — by-construction consistency sentinels (expected-0; regression guards for the merge/HITL tier):**

| Point | Why 0 is by construction |
|---|---|
| **DQ-A01** | `from_person_id` and `from_address` come from the *same* parsed object; the `person_email` key *is* `normalize_email(that address)` — so `normalize(stored)==key` is tautological today. 12,733 from + 27,257 recipient links = **coverage**. |
| **DQ-A03** | the resolver writes the key *through* `normalize_email`, so a stored key already equals its normal form. |
| **DQ-A04** | `_get_or_create_person` always inserts the `person_email` with the person in one nested txn. |
| **DQ-K01** | `_resolve_company` always links a company for a non-generic person. |
| **DQ-J01/J02** | every person/company is created *for* the message being stored (no deletions ran), so none is unreferenced. |

These sentinels are **worth keeping** — once the deferred name-merge / HITL tier starts *reassigning*
`from_person_id` across persons, A01/J01 become the tripwire that catches a mis-reassignment. But today
they prove the code is internally consistent, not that the data is independently correct.

---

## 4. Findings (by severity)

### HIGH

**DQ-K04 — 38% of persons have no name (real people).** 472 / 1,227 persons have a blank/null
`display_name`. **0 of them are automated senders** — a sample is `a***@ethera-tech.com` (a colleague),
`office@dik.bg`, `d***@apis.bg`, `yani_lozanov@outlook.com` (the owner's own personal address). They are
real humans first seen as a bare `To`/`Cc` address (or a `From` with no display phrase); `display_name`
is set **once at creation and never improved** (`EntityResolver._get_or_create_person`), and
`person_alias` is never written, so a later email carrying the name cannot repair them. *Impact:* a dossier
keyed on a nameless person is far less useful; entity-merge later has no name signal. *Remediation:*
back-fill `display_name` on later sightings (take the best non-empty name seen) and start writing
`person_alias`.

**DQ-H01 — ~9 GB of document text dropped (4,202 business docs).** Attachment text extraction returns
`None` for every binary format and the bytes are then discarded (`attachment_extractor.extract_text` +
`EmailIngestService._store_attachments`). On the real corpus:

| type | count | bytes |
|---|---|---|
| image/png | 9,109 | 4.38 GB |
| application/pdf | 2,306 | 2.37 GB |
| ms-tnef | 2,355 | 0.61 GB |
| Word (.docx) | 1,641 | 0.59 GB |
| octet-stream | 220 | 0.70 GB |
| Excel/PowerPoint | 167 | 0.49 GB |

4,202 of these are business documents (PDF/Word/Excel/PPT) whose text is **permanently lost** in the
current path. *Status:* **already tracked — CA-CONN-04** in `FIX_BEFORE_PROD.md`; this audit quantifies
the magnitude. *Impact for RAG:* the richest content (contracts, invoices, statements) is invisible to
retrieval. Must land before a production mailbox is ingested with byte-discard on.

**DQ-B05 — ~40% of the corpus is duplicate rows (the dedup design's blind spot).** 13,635 rows hold only
**8,240 unique logical emails** (distinct Message-IDs); **5,395 rows (39.6%) are redundant copies**, in
**2,533 true-duplicate groups** (same Message-ID + from + subject), up to **9 copies** of one email. Cause:
the C02 fix keys `dedup_key` on `sha256(raw_bytes)`, and its docstring assumes "the same logical email
re-fetched / seen in two folders" has identical bytes — **but it does not**: each IMAP folder's copy of a
message differs by a header (Received/X-folder), so the hash differs and the row is stored again. (Only **8**
of the 2,533 groups are genuine content collisions — DQ-G01; the rest are pure cross-folder dupes.) *Impact:*
for RAG + dossiers this inflates everything ~1.65× — retrieval returns the same email many times, entity
relationship-strength (edge counts) is over-weighted, and storage/embedding cost is ~40% wasted. *No data
is lost* (over-storage, not loss), so it is HIGH not Critical. *Remediation:* dedup on a **content
identity** that is stable across folders — Message-ID when present, else a hash of normalized
headers+body — instead of raw wire bytes; this also keeps the C02 anti-poisoning property if combined with
a content (not Message-ID-only) hash. *(Correction: my first B05 metric reported "10" — a `LIMIT 10` in the
query capped the count, not just the display; the true figure is 2,533 groups / 5,395 rows.)*

**DQ-D01 — 77 counterparty domains lost to role-address suppression.** A role local-part returns `None`
from `resolve_participant` **before** company resolution, so a domain seen only via `info@`/`office@`/
`kontakt@`/`no-reply@` mints **neither a person nor the company**. 77 non-generic domains are absent from
the company graph, e.g. `qwoted.com` (648 msgs), `fathom.video` (44), `gitlab.com` (34), `docker.com`
(26), `dik.bg` (26), `account.hostinger.com` (24). *Mixed nature, verified by sampling:* many are SaaS
notification domains (`no-reply@qwoted.com`, `no-reply@fathom.video`) that arguably *should not* be
companies — but **`office@dik.bg`** is a genuine Bulgarian counterparty lost purely because it was only
contacted at a role mailbox. *Remediation:* split "should this be a Person?" from "should we observe this
Company/domain?" — a role address is strong evidence the **domain** exists even when it is not a person.

### MEDIUM

**DQ-K03 — long-tail noise: 72% single-person companies, 22% degree-1 persons.** 354/495 companies have
exactly one person; 275/1,227 persons appear in exactly one message. Combined with K04, the graph head is
fine but the tail is thin — many low-information nodes that add little and dilute entity ranking.

**DQ-C01 — automation senders mint 38 persons.** `is_automated` is computed but the resolver never
consults it; 115 automated messages carry a `from_person_id`, producing 38 distinct "persons" that are
machines (their local-part dodged the role list). *Remediation:* gate person-hood on `is_automated` too,
or expand the role list.

**DQ-C02 — 73 persons are routing identities.** 73 persons exist *only* as `reply_to`/`sender`
recipients (never from/to/cc) — `_extract_recipients` resolves those header kinds into people. These are
list/relay/no-reply identities masquerading as humans.

**DQ-B02 / DQ-B03 — bot-name collisions + subdomain fragmentation.** 8 display-names are shared by
multiple persons — `ethera-technologies/gbs` (11 persons), `aspar / yani-gang-assist-ui` (9), `Atlassian`
(8) — GitHub/Atlassian notification senders where many no-reply addresses share one name. 12 registrable
domains are split across companies — **`atlassian.net` → 4 companies**, `google.com` → 3, `github.com`,
`gitlab.com` — because the company key is the full host with no eTLD+1 reduction.

**DQ-G01 / DQ-B06 — Message-ID collisions + duplicate recipient edges.** 8 Message-IDs are reused across
*different* subjects (over-merge risk if threads key on Message-ID — distinct from the 2,533 same-content
cross-folder dupes in DQ-B05 above). 183 duplicate `(email, kind, address)` recipient edges within single
messages further inflate relationship strength.

**DQ-E02 — mailbox-centric direction.** Direction mix `outbound 8,308 / inbound 5,247 / null 80`; **505
inbound messages are from an internal person** (a colleague on `ethera-tech.com`), because direction
compares `From` only to the single mailbox address, not the tenant's people.

**DQ-F03 — 177 messages: big bytes, no words.** `word_count<5 AND size>50 KB` — incl. 48 MB photo emails
(`wc=0`). Mostly genuine photo/attachment mail, but the pattern also catches the plain-part-hides-HTML
risk; for RAG these are near-empty bodies on heavy messages.

**DQ-G02 — thread fragmentation.** 12,404/25,509 `references` (49%) and 2,192/6,577 `in_reply_to` (33%)
point at Message-IDs absent from the corpus. Heavily caveated by corpus completeness (threads reference
mail outside the dump), but a real fragmentation signal for thread reconstruction.

### LOW / INFO

- **DQ-C03** — 1 freemail-typo company: `gmaill.com` (typo of gmail.com) became a bogus company.
- **DQ-F02 / DQ-H02** — 41 message bodies and 9 attachment texts contain replacement/mojibake characters
  (charset edge cases; attachments decode utf-8-only).
- **DQ-F01** — 8 persons use their address as the display name (`v***@barinsports.com`).
- **DQ-I01 / I02 / I03 — dead fields confirmed:** `company_domain.source` 100% NULL (495/495) while
  `person_email.source` 100% set; `person_alias` 0 rows; `language` 0/13,635. Latent inconsistency /
  unused columns.
- **DQ-I04** — headers JSONB avg 2.2 KB / max 16 KB; 5,347 messages carry DKIM/Authentication-Results/
  Received — bloat + routing metadata that must not feed retrieval.
- **DQ-H03** — 3 zero-byte attachments.
- **DQ-B01** — 0 Gmail dot/+ duplicate persons in LIVE (the conservative normalizer's known under-merge
  did not surface in this corpus; **confirmed reproducible in SEED**, below).

---

## 5. Full results matrix

`✅ clean · ⚠ concern · ❌ defect · 📋 documented elsewhere`. LIVE = 13,635-email real corpus.

| Point | LIVE | Verdict | Point | LIVE | Verdict |
|---|---|---|---|---|---|
| A01 invariant | 0/40k links | ✅ | E02 dir internal | 505 inbound-internal | ⚠ |
| A03 normalized | 0 | ✅ | E03 auto false-neg | 0 | ✅ |
| A04 person/email | 0 | ✅ | I01 company source | 495/495 NULL | ⚠ dead |
| K01 person no-company | 0 | ✅ | I02 person_alias | 0 | ⚠ dead |
| K03 degree-1 | 22% / 72% co. | ⚠ | I03 language | 0/13,635 | ⚠ dead |
| K04 blank name | 472 (38%) | ❌ | I04 header bloat | 5,347 w/ DKIM | ⚠ |
| J01/J02 orphans | 0 / 0 | ✅ | F01 dirty name | 8 addr-as-name | ⚠ |
| B01 gmail dup | 0 (live) | ✅ | F02 body mojibake | 41 | ⚠ |
| B02 shared name | 8 (bot) | ⚠ | F03 big/no-words | 177 | ⚠ |
| B03 subdomain frag | 12 (atlassian×4) | ⚠ | F04 failed parse | 0 | ✅ |
| B05 dup messages | 5,395 (40%) | ❌ | F06 date sanity | 0 (80 null) | ✅ |
| B06 dup recip edges | 183 | ⚠ | F07 subject ctrl | 0 | ✅ |
| C01 auto persons | 38 | ⚠ | G01 msgid collision | 8 | ⚠ |
| C02 reply/sender | 73 | ⚠ | G02 dangling refs | 49% / 33% | ⚠ (corpus) |
| C03 typo company | 1 (gmaill) | ⚠ | H01 doc text lost | 4,202 (~9GB) | 📋 CA-CONN-04 |
| C04 malformed dom | 0 (live) | ✅ | H02 att mojibake | 9 | ⚠ |
| D01 role kills company | 77 domains | ❌ | H03 att meta | 3 zero-byte | ⚠ |
| D03 absent domains | 77 | ⚠ | E01 internal drift | 0 | ✅ |

---

## 6. Seed-arm confirmation (cleaning logic responds exactly as designed)

Every crafted defect fired, proving the LIVE behaviours are explainable, not random:

| Mechanism | Seed result |
|---|---|
| B01 Gmail dot/+ under-merge | `jsmith@gmail.com` → **3 persons** |
| B02 name collision | John Smith ×3, Anna Berg ×2, Peter Klein ×2 |
| B03 subdomain frag | `lieferant.de` + `mail.lieferant.de` → 2 companies |
| B05 byte-variant dup | `b05-dup@seed` stored ×2 |
| C01 automation person | 2/2 automated minted a person |
| C02 reply_to/sender person | 2 |
| C04 malformed domain | `localhost` company |
| D01 role kills company | `kunde-gross.test`, `grosskunde.test` lost |
| E01 generic-mailbox internal | 1 external-but-internal (gmx.de tenant case) |
| F02 body mojibake | 1 (`GrÃ¼ÃŸe`) |
| F03 plain-hides-HTML | 1 (`wc=3`, 59 KB, rich HTML body hidden) |
| F04 deep-nest degrade | 1 `parse_status=failed` (RecursionError, stored not dropped ✅) |
| F06 date sanity | epoch ×1, future ×1, skew ×1 |
| F07 subject control char | 1 |
| G01 Message-ID collision | `collide-g01@seed` ×2, distinct from/subject |
| H01 binary text dropped | PDF + octet-stream → NULL; text/csv extracted |
| H02 attachment mojibake | 1 (latin-1 CSV decoded utf-8) |
| H03 zero-byte attachment | 1 |

---

## 7. Reconciliation with the plumbing pass (prior C02)

The prior pass listed **C02** ("dedup poisoning via colliding Message-ID → silent skip") as a pending
fix. Current `email_parser.py` keys `dedup_key` on **`sha256(raw_bytes)` always** (never Message-ID),
which is exactly the anti-poisoning design — so **C02-as-described is already mitigated**: a colliding
Message-ID with different bytes hashes differently and is **stored, not skipped** (confirmed live: 8
Message-ID collisions all retained as distinct rows — DQ-G01). **But the C02 fix has a large measured
side-effect: DQ-B05** — keying on raw wire bytes means the same email seen in N IMAP folders is stored N
times (each folder copy differs by a header), producing the 39.6% duplication. So the prior pass's C02 and
this pass's B05 are two ends of the *same* design choice: raw-byte dedup buys poisoning-resistance at the
cost of cross-folder over-storage. The fix that satisfies both is a **content-identity** hash (normalized
headers+body, or Message-ID with a content tie-breaker), not raw bytes and not Message-ID alone. The stale
`email.py` model docstring still says "Message-ID, else hash" and the parser docstring claims a re-seen
email "still collides" — both are now contradicted by the data and worth correcting.

## 8. FIX_BEFORE_PROD candidates (this audit's contribution)

Genuinely-actionable, not already tracked:
1. **DQ-B05** dedup on a **content identity** stable across IMAP folders (Message-ID + content hash, or a
   normalized headers+body hash) instead of raw wire bytes — removes ~40% duplicate rows while keeping the
   C02 anti-poisoning property. *(Highest-leverage: it inflates every downstream count.)*
2. **DQ-K04** back-fill `display_name` / write `person_alias` on later sightings (38% nameless real people).
3. **DQ-D01** split person-creation from company-observation so role-only real counterparties still form a company.
4. **DQ-C01/C02** gate person-hood on `is_automated` + exclude `reply_to`/`sender`-only identities.
5. **DQ-B03** reduce company key to eTLD+1 (stop `atlassian.net`→4 companies).
6. **DQ-I01** set `company_domain.source` (or drop the column) — and `person_alias`/`language` are unwired.

Already tracked: **DQ-H01 = CA-CONN-04** (binary attachment extraction) — magnitude here is ~9 GB / 4,202 docs.

## 9. Limitations

- **Per-message and per-attachment counts are inflated ~1.65× by the DQ-B05 cross-folder duplication**
  (13,635 rows / 8,240 logical emails). Message-level figures (K03 degree, B06, C01, H01 attachment
  counts, header-bloat totals) should be read against 8,240 logical emails, not 13,635. **Person/company
  counts are NOT inflated** (deduped by email/domain), so K04 (38% nameless) and the entity-graph metrics
  stand as-is.
- **Metric-reporting caveat:** the B05 and several "top-N" displays were `LIMIT`-capped; B05's headline
  count was corrected from a capped "10" to the true 2,533 groups / 5,395 rows after the Message-ID census
  surfaced the discrepancy. Other capped displays (B02/B03/D01 "top shown") report the true total in the
  count and only cap the examples.
- LIVE is **one mailbox on one real domain** — multi-connection internal-drift (E01) and generic-domain
  tenant (D02) are only exercised in SEED.
- **DQ-G02** dangling-reference rates are inflated by corpus incompleteness (the dump is not the full
  thread universe); treat as directional, not absolute.
- Heuristic checks (B03 registrable = last-2-labels; C03 Levenshtein-1 to a freemail list) are
  approximate; the cited examples were eyeballed.
- "Noise vs correct exclusion" (D01 SaaS domains, K03 singletons) is a judgement call surfaced for review,
  not an automatic defect.
