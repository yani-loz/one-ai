# Data Quality Audit — IMAP Connector — 2026-06-14 21:07:57

**Run:** Manual, max-effort, single-orchestrator (the `Agent`/team tools were unavailable in this
execution context, so all scouting was performed directly with SQL + code reads — no findings were
delegated). Duration ≈ one deep pass over the full corpus + code.
**Database:** `oneai` @ localhost:5432 · org `d1500000-0000-0000-0000-000000000001` (dev IMAP corpus)
**Corpus:** 13,635 `.eml` on disk → 8,386 stored, 5,249 skipped (dedup), 0 failed.
**Prior report:** none — this is the **first** IMAP audit (no trend baseline). The "Baseline Metrics"
section below seeds the next run's diff.
**Total findings:** 🔴 1 critical · 🟠 2 high · 🟡 5 medium · 🟢 6 info/observations

---

## TL;DR

The IMAP connector's **storage core is production-grade**: referential integrity is pristine (zero
orphans, zero cross-org bleed across 9 tables, composite `(org_id, …)` FKs make cross-tenant
references structurally impossible), dedup at the v5 content-identity key collapsed cross-folder
copies from a prior 39.3% duplication down to **~0.15–0.3% residual** (12–26 missed dups out of
8,386), the **secrets-masking gate is both precise and high-recall** (256 real secrets redacted,
**zero** structured-token survivals, zero false positives observed), and attachment extraction is
honest and complete enough for retrieval (the text-bearing↔non-null-text contract holds with **zero**
violations; scanned PDFs are honestly flagged, never silently empty; xlsx structured capture is
clean JSONB).

**The one thing the human must act on before production: the ingest path has _no folder filtering_,
so 2,355 blocklist-only emails — 2,133 from Trash, 190 spam, 32 unsent Drafts — leaked into the
knowledge base. That is 28.1% of the stored corpus.** For any company that installs One AI, this
means deleted mail, junk, and unsent drafts would surface as organizational knowledge. This is the
single blocker; everything else is hygiene or deferred-by-design.

The secondary readiness gap is **entity fragmentation**: the v1 deterministic resolver is strictly
1-person:1-email, so the same human with multiple addresses becomes multiple person rows (the
mailbox owner himself fragments into 4 identities, 3 wrongly marked external). This is documented as
deferred in the code, but it caps "show me everything from person X" queries.

---

## Findings

### 🔴 Critical

#### 1. Blocklist folders (Trash / Spam / Drafts) are ingested wholesale — 28.1% of the stored corpus is discarded/junk/unsent mail

- **Root cause:** The ingest path has **no folder-awareness and no blocklist filter anywhere**.
  `email_ingest_service.py:ingest_email` (lines 72–113) accepts `raw_bytes` and unconditionally
  parses → resolves → stores; it never receives or inspects a folder name. The disk-ingest driver
  (`scripts.ingest_imap_dump`) walks every `.eml` under the mailbox root, including
  `INBOX.Trash` (7,502 files), `INBOX.spam` (191), `INBOX.Junk`, and `INBOX.Drafts` (33). The v5
  dedup key is **folder-independent by design** — it correctly collapses a Trash *copy* of a live
  INBOX email onto the INBOX row, but it does **nothing** to exclude mail that exists *only* in a
  blocklist folder. There is no equivalent of an `EXCLUDED_FOLDERS` set in the fetch/ingest seam.
- **Evidence:** Mapping every disk `.eml`'s normalized Message-ID to its folder class, then
  intersecting blocklist-only Message-IDs with the stored `email_message.message_id` set:
  - **2,355 blocklist-only emails are STORED** (≈ **28.1%** of 8,386):
    - **2,133 Trash-only**, **190 spam-only**, **32 Drafts-only**.
  - Sample leaked spam (qwoted.com PR-request blasts the user trashed):
    `notifications@qwoted.com | "Forbes: Sources needed: AI infrastructure…"`,
    `… | "TechNewsWorld: Looking for experts on AI slop"` — `is_automated=False`, fully indexed.
  - The 32 Drafts manifest as the **80-row NULL-`from_address` cluster**: Outlook unsent drafts have
    no `From`/`Date`/`Received` header (Outlook stamps those at send time) and carry a placeholder
    Message-ID like `017401dc06b4$e967b4c0$bc371e40$@Domain` (note the literal `@Domain`). They still
    carry real `Subject`/`To`/body and are stored as first-class emails with `direction=NULL`.
- **Impact:** Every company that runs the connector on a real mailbox will have its AI ingest
  **deleted email** (content the user explicitly discarded — a GDPR/trust problem), **spam**
  (poisons retrieval and the entity graph — qwoted's PR spam alone minted reporter/outlet noise),
  and **unsent drafts** (half-written, sender-less, possibly contradicting the final sent version).
  "Summarize what we agreed with X" can surface a discarded draft as fact.
- **Fix:** Introduce a folder-classification + blocklist gate in the fetch/ingest seam — generic, not
  data-specific:
  - Add an `EXCLUDED_FOLDER_ROLES = {"trash","junk","spam","drafts"}` set resolved via **IMAP
    SPECIAL-USE flags (RFC 6154)** (`\Trash`, `\Junk`, `\Drafts`) with a name-heuristic fallback
    (`Trash|Deleted|Spam|Junk|Bulk|Drafts`, case-insensitive, locale-aware list extensible like the
    existing `_GENERIC_DOMAINS`). The live fetcher (`imap/sync/imap_fetcher.py` /
    `fetch_planner.py`) should skip these folders; the disk-ingest driver should classify by path and
    skip the same set.
  - Pass the originating folder into `ingest_email(..., folder_role: str)` and make the service
    **refuse** an excluded role as defence-in-depth, so the policy is enforced at the storage
    chokepoint regardless of which driver calls it.
  - Re-ingest the dev corpus after the gate lands; expect ~2,355 fewer stored rows.
- **Found by:** orchestrator (disk-folder inventory → Message-ID×folder map → DB intersection).

---

### 🟠 High

#### 2. Person identities fragment — same human → multiple person rows; mailbox owner split 4 ways, 3 wrongly "external"

- **Root cause:** The resolver is **v1 deterministic-only** — `entity_resolver.py` matches/creates a
  person purely by exact normalized email (`_get_or_create_person`, lines 149–165). The cross-person
  name-merge tier is **explicitly deferred** (module docstring lines 6–8: "the ambiguous 'pit' +
  HITL + cross-person name-merge tiers are deferred — they need provenance/confidence columns not in
  the 3a schema"). Result: `person`:`person_email` is strictly **1:1** (1,140 = 1,140, 0 persons
  with >1 email). Any human using ≥2 addresses becomes ≥2 person rows, and `is_internal` is computed
  per-address, so the same person can be internal on one address and external on another.
- **Evidence:**
  - The mailbox owner fragments into **4 persons**: `yani.lozanov@ethera-tech.com` (internal, 3,070
    sent) + `yani_lozanov@outlook.com` (**external**, 54 sent) + `yanilozanov@ocenki.bg`
    (**external**) + `yani_lozanov_spam@outlook.com` (**external**). The owner is classified external
    on 3 of his 4 identities.
  - 20 display-names are shared by >1 person row: `ivaylo vatovski` ×5, `elena lehmann` ×4,
    `stilyana vargova` ×4, `yani lozanov` ×4, `georgi georgiev` ×3, …
- **Impact:** "Show me everything from/about person X" returns only the fragment matching the queried
  address; cross-identity history doesn't aggregate. Worse, the per-address `is_internal` split
  corrupts inbound/outbound reasoning and "internal vs external" filters — the owner's own outbound
  from a secondary address reads as an external contact.
- **Fix:** This is the deferred confidence-tier work. Minimum infrastructure step that helps every
  install: add a **same-person merge signal** keyed on (normalized display-name token-set + at least
  one shared strong signal — e.g. an `In-Reply-To`/`References` thread co-membership, or one address
  appearing as an alias on the other) behind a HITL confirmation, plus the provenance/confidence
  columns the docstring calls for. Until then, document the 1:1 limitation for the Ask layer so it
  queries by *name token-set ∪ all addresses*, not a single address.
- **Found by:** orchestrator (person↔email cardinality + display-name duplicate grouping).

#### 3. Raw `headers` jsonb is stored without passing through the secrets-masking gate

- **Root cause:** `redact_secrets` is applied to `body_text` and attachment `extracted_text` only
  (`attachment_extractor.py:_mask_secrets`, `email_parser` body path). The full raw header set is
  persisted verbatim into `email_message.headers` (jsonb, NOT NULL) and is **never** scanned. The
  masking gate's own docstring (`redact.py` lines 5–9) scopes itself to "email body_text AND
  attachment extracted_text" — headers are out of scope by omission, not by a decision that headers
  are secret-free.
- **Evidence:** 28 stored rows carry a token-shaped value in `headers::text`. **On this corpus all 28
  are benign** — every match is a `jwt`-shaped (`eyJ…`) base64url payload inside
  `List-Unsubscribe` / `List-Help` / `x-mailgun-variables` URLs (public unsubscribe tokens, designed
  to be clicked), and there are **zero** AWS/OpenAI/Google/PEM matches and zero `Authorization:` /
  `Bearer` headers. So **no live secret leaked through headers in this dataset** — but the gap is
  structural: a future mailbox carrying an `Authorization:`, `X-API-Key:`, or webhook-token header
  (common in automated/transactional mail) would store it un-redacted and queryable.
- **Impact:** Latent credential-leak surface. Headers are stored and SQL-queryable today; once the
  Ask layer embeds or surfaces header metadata, an un-masked header secret would reach a provider /
  a user. Low likelihood, high blast radius.
- **Fix:** Run a **header-aware redaction** over `headers` before storage — either pass the
  serialized header block through `redact_secrets`, or (better, to preserve structure) drop/redact a
  small denylist of secret-bearing header names (`authorization`, `x-api-key`, `x-auth-token`,
  `cookie`, `proxy-authorization`) before persisting `headers`. Generic; helps every install.
- **Found by:** orchestrator (token-shape regex over `headers::text`, then per-header attribution).

---

### 🟡 Medium

#### 4. `is_role_address` false-positives suppress real people whose localpart contains a role word under `_`/`-`

- **Root cause:** `address_rules.py:is_role_address` matches a BROAD role word
  (`finance`, `accounts`, `billing`, …) when it is a `-_+`-delimited token of the localpart
  (`_LOCALPART_NAME_SAFE_SPLIT`, line 115; check at lines 163–164). The dot-split is name-safe, but
  the underscore-split is not: `factis_finance` → tokens `[factis, finance]` → `finance` ∈
  `_ROLE_WORDS_BROAD` → classified as a role address → **never minted as a Person**.
- **Evidence:** `factis_finance@yahoo.com`, display name **"Kiril Kirilov"**, `is_automated=False`,
  **113 emails** — a real human — has **zero** `person_email` rows. Verified in-container:
  `is_role_address("factis_finance@yahoo.com") = True`; `accounts_payable@…`, `newsletter.editor@…`
  also fire (those are arguably correct, but `factis_finance` is a clear false positive).
- **Impact:** Real counterparties using a `name_roleword` or `word_roleword` personal/business
  address vanish from the person graph and from company linkage. "Who is Kiril Kirilov / who handles
  finance at X" misses 113 emails of evidence. Affects any install with `_`-joined business addresses.
- **Fix:** Treat the underscore the same as the dot for BROAD role words — i.e., only fire a BROAD
  role word when it is the **whole localpart** or appears with an automation co-signal, not merely as
  one `_`-token beside a name token. Keep AUTOMATION tokens (`noreply`, …) firing under any
  delimiter. The fix narrows `_LOCALPART_NAME_SAFE_SPLIT` participation for the broad tier.
- **Found by:** orchestrator (top non-automated `from_address` with NULL `from_person_id` →
  in-container `is_role_address` reproduction).

#### 5. xlsm dispatch misses macro-enabled spreadsheets due to a case mismatch

- **Root cause:** `attachment_extractor.py:_dispatch_extract` lowercases the content type
  (`content_type = attachment.content_type.lower()`, line 194) and then compares against
  `_XLSX_CONTENT_TYPES` (lines 104–109), which contains the **mixed-case** literal
  `"application/vnd.ms-excel.sheet.macroEnabled.12"`. After `.lower()`, the incoming
  `…macroenabled.12` never equals the frozenset's `…macroEnabled.12`, so the file falls through to
  `unsupported_format` instead of the xlsx extractor.
- **Evidence:** Reproduced: `"application/vnd.ms-excel.sheet.macroenabled.12".lower() in
  _XLSX_CONTENT_TYPES → False`. In the corpus, 1 attachment with this exact content type is wrongly
  `unsupported_format` (no structured grid, no text) instead of `extracted`.
- **Impact:** Every macro-enabled `.xlsm` (common in finance/ops mailboxes) silently loses both its
  text render and its `xlsx-grid-v1` structured capture. The same latent bug applies to any other
  mixed-case literal compared against a lowercased input in these dispatch frozensets.
- **Fix:** Lowercase every literal in `_XLSX_CONTENT_TYPES` (and audit the other dispatch sets) so
  comparisons are consistently case-folded. One-line normalization; helps every install.
- **Found by:** orchestrator (`unsupported_format` × content_type tally → reproduction).

#### 6. `language` column is permanently NULL — declared, modelled, never populated

- **Root cause:** Language detection is unimplemented. `email_parser.py` hardcodes `language=None`
  at both the failure path (line 169) and the success path (line 218); the field threads through
  `email_ingest_service._build_message` (line 143) to the column unchanged.
- **Evidence:** 8,386 / 8,386 rows have `language IS NULL` (100%).
- **Impact:** A dead column. Any Ask-layer feature keying on language (routing to a locale-specific
  prompt, "show me the German emails") has nothing to read — notable because the corpus is heavily
  bilingual BG/EN. Not harmful, but a silent capability gap a reader of the schema would assume works.
- **Fix:** Either implement detection (e.g. a fast `lingua`/`fastText`-style detector over `body_text`
  at parse time, populating the existing `String(16)` column) or remove the column until the feature
  lands so the schema doesn't over-promise.
- **Found by:** orchestrator (null-coverage scan + `email_parser` source).

#### 7. `is_automated` (message flag) and the person-hood automation gate use two divergent token vocabularies

- **Root cause:** Two automation token sets exist and have drifted. `flags.py:_AUTOMATED_TOKENS`
  (feeds the stored `is_automated` column via `is_automated_sender`) = `{noreply, donotreply,
  mailerdaemon, postmaster, bounce, bounces}`. `address_rules.py:_AUTOMATION_TOKENS` (feeds
  person-hood suppression) additionally includes `{notifications, notification, notify, automated,
  daemon, mailer, delivery, newsletter}`. The split between `is_automated_sender` and
  `is_automated_origin` is intentional and well-documented; the **token-list divergence between the
  two modules is not** — it's just two hand-maintained frozensets.
- **Evidence:** `notifications@qwoted.com` (313 emails) is stored `is_automated=False` (flags.py set
  lacks `notifications`) yet is correctly **not** minted as a person (address_rules set has it). So a
  "hide automated mail" filter still surfaces 313 qwoted notification emails.
- **Impact:** "Exclude automated/notification mail" under-filters by exactly the senders the entity
  layer already treats as non-human. Inconsistent behavior across two views of the same fact.
- **Fix:** Single-source the automation-token vocabulary (one shared frozenset imported by both
  `flags.py` and `address_rules.py`), keeping the *function-level* sender/origin distinction. Generic.
- **Found by:** orchestrator (is_automated cross-tab vs `is_role_address` behavior + both sources).

#### 8. Residual cross-route duplicates the v5 key still misses (~12–26 rows / 0.15–0.3%)

- **Root cause:** When the SAME logical email is delivered to the owner via TWO infrastructures
  (e.g. one copy routed through Office365, the other through the `ethera-tech.com` host), the two
  physical copies share Message-ID, From, Subject, instant, recipients, body_text, and attachment
  content-hashes — but their **`text/html` alternative differs** because each transport re-renders
  the HTML (regenerated MIME boundaries, re-wrapped encodings, injected spam/host markup). The
  `_html_body_digest` component (`dedup_key.py:248–266`) hashes that decoded-but-transport-mutated
  HTML, so the two copies get different keys and both are stored. The component is deliberately
  sensitive (it guards against a decoy that differs only in HTML), but on genuine cross-route
  delivery it over-splits.
- **Evidence:** 11 groups / **12 excess rows** are identical on *every observable content field*
  (msgid + from + subject + UTC instant + body + recipient-set + attachment-hashes) yet stored
  separately; broadening to (msgid, body) gives 24 groups / 26 excess. Confirmed mechanism by reading
  raw `.eml`: for one `tlyubenov09@gmail.com` "Договор гласов бот" message, the plain bodies pair up
  (md5 `d91723f7`×2, `d1f5bae3`×2) while the HTML digests are all distinct (`318858cb`, `86ce03bf`,
  `3c7396c0`, `0f433493`); the header diff shows one copy carries the full Office365
  `X-MS-Exchange-*` trail and the other the `tesla.superhosting.bg` + `X-Spam-*` trail.
- **Impact:** Tiny in volume but it surfaces in retrieval: a thread view shows "Договор гласов бот"
  twice, so an LLM summarizing the thread can double-count. The code's own invariant is "NEVER
  under-dedup on serialization variance" — this is exactly that class, just at the HTML layer rather
  than the boundary layer the v5 work already fixed.
- **Fix:** Make the HTML component **transport-robust**: digest the **html2text-flattened** HTML
  (same `html_to_text` the body path uses) instead of the raw decoded HTML, mirroring how v5 made the
  TNEF body robust by flattening the RTF. Flattening erases transport-injected markup
  (boundaries, antispam banners, re-wrapping) while still splitting genuine content differences — the
  identical proof the TNEF-flatten fix relied on. Verify on these 11 groups before/after.
- **Found by:** orchestrator (multi-field duplicate grouping → raw `.eml` HTML-digest comparison).

---

### 🟢 Info / Observations

- **O-1 — Referential & tenant integrity is flawless.** Zero orphans across all 13 FK relationships;
  zero rows outside the dev org; zero child→parent cross-org references; zero uniqueness violations.
  The schema enforces this *structurally* — every FK is composite `(org_id, …)` referencing
  `(org_id, id)`, so a cross-tenant reference cannot be inserted. CHECKs pin `extraction_status`,
  `parse_status`, `direction`, `recipient.kind`, and `source='imap'`. This is the strongest part of
  the connector and needs no action.
- **O-2 — Masking gate is precise AND high-recall on this corpus.** 256 secrets redacted (139 bodies
  / 23 attachments; kinds: credential 162, jwt_token 61, openai_key 29, google_api_key 4). The
  attachment count cross-checks exactly: 23 rows with `[REDACTED:]` = 23 rows with
  `secrets_redacted=N` in `extraction_detail`, ΣN = 68 = markers found. **Zero** survivals of any
  structured token class (AWS/OpenAI/Google/GitHub/Slack/Stripe/JWT/PEM) in body or attachment text.
  **Zero** high-entropy keyed-value misses. **Zero** false positives observed — every
  `[REDACTED:credential]` sampled was a true secret (`Bearer Token:`, `password=`, `client_secret:`,
  `SECRET_KEY=`, connection-string `password=`) with the key NAME and surrounding prose preserved.
- **O-3 — Attachment extraction honest-NULL contract holds with zero violations.** No non-text-bearing
  status carries text; no text-bearing status lacks text. 110 `scanned_pending_ocr` PDFs all have
  `text=NULL` (honestly deferred, not silently empty); 0 large (`>100 KB`) PDFs are wrongly `empty`.
  62 xlsx structured grids are all `xlsx-grid-v1`, 0 NaN/Infinity poison, 0 content-type mismatch,
  139 sheets with typed cells (Cyrillic preserved). Every text-bearing row names its extractor.
- **O-4 — Entity graph is clean where it minted.** 0 generic free-mail domains leaked as companies,
  0 punycode/non-ASCII domains leaked (the M-8 IDN quarantine works), 0 company key collisions, 0
  domain→multiple-company. eTLD+1 folding verified live (`bg.ibm.com`→`ibm.com`), SaaS suffixes stay
  distinct (`ethera-tech.atlassian.net` is its own identity). The legit `breeze.no` minted correctly
  while its homoglyph form would be quarantined. `apis.bg` person-join count == raw from_address count
  (101 == 101) — perfect company→people→email coherence for non-role senders.
- **O-5 — Body text is markup-clean.** 0 raw HTML tags, 0 RTF control words, 0 `Â` mojibake. Residual
  artifacts are minor: 29 rows (0.35%) carry `=?utf-8?Q?` RFC2047 encoded-words *inside forwarded
  bodies* (quoted original headers pasted into the body — cosmetic, not a header-decode failure), 15
  rows carry 2× U+FFFD each from the charset chain's honest final fallback. Quoted-reply chains
  (`-----Original Message-----` 160×, `On … wrote:` 1,529×) are normal email content for the future
  chunking layer to strip, not a storage defect.
- **O-6 — Low-quality canonical display names.** "First non-empty name wins" backfill (DQ-K04) lets a
  poor first sighting stick: 17 persons have a full email address as their `display_name`
  (`albena@dataplus-bg.com`), 25 look like bare localparts (`todor.dapev`, `t.railo`), 1 carries an
  emoji (`Моника❤️`), and 196 `from_name` values disagree with the resolved person name. Aliases
  capture the better alternatives, but the canonical name shown to the LLM can be weak. Consider a
  "name quality" preference (prefer `First Last` with a space over a localpart/address) in the
  backfill.

---

## Novel Discoveries

1. **The 80-row "NULL `from_address`" cluster is unsent Outlook Drafts.** They have `Subject`/`To`/
   body but no `From`/`Date`/`Received` (Outlook stamps those at send) and a placeholder Message-ID
   ending `…$@Domain`. This was the thread that unravelled the Critical finding — drafts have no
   sender, so they were the visible symptom of the no-folder-filter bug.
2. **`is_internal` is computed per-address, not per-person** — the mailbox owner reads as "external"
   on 3 of his 4 fragmented identities. A direct, surprising consequence of the 1:1 resolver that an
   "internal vs external" filter would get wrong for the owner himself.
3. **The HTML digest splits cross-route duplicates.** The v5 fix flattened the TNEF RTF body to make
   it transport-robust but left the `text/html` alternative *raw* — so the exact failure class v5
   solved for TNEF re-emerges, one MIME layer over, for plain cross-route delivery (Finding 8). The
   fix is the same primitive (flatten before digest).
4. **qwoted.com PR-spam minted entity noise.** Leaked spam doesn't just sit in `email_message`; the
   190 spam-only rows fed the resolver, so spam senders/outlets accreted into the person/company
   graph. Blocklist leakage and entity-graph pollution are the same root cause seen from two tables.
5. **34 BCC recipients on *inbound* mail.** You normally only see Bcc on your own Sent copy; 34
   inbound rows carry a parsed Bcc header. Faithful to the `.eml` (not a defect), but a curiosity —
   likely the owner being Bcc'd with the header surviving, or self-addressed sends.

---

## Improvement Suggestions (ranked by impact / effort)

1. **Folder blocklist gate (Finding 1) — highest impact, low effort.** SPECIAL-USE + name-heuristic
   exclusion of Trash/Junk/Spam/Drafts in the fetch/ingest seam, enforced again at
   `ingest_email`. Eliminates 28% corpus pollution for every install. *Risk: low* (additive filter;
   re-ingest needed).
2. **Flatten the HTML digest (Finding 8) — high integrity, low effort.** Reuse `html_to_text` in
   `_html_body_digest`. Closes the last known under-dedup class. *Risk: low* (verify the 11 groups +
   the decoy/HTML-only-difference guard still splits a genuine HTML-only diff after flattening).
3. **Header-aware redaction (Finding 3) — closes a latent breach surface, low effort.** Redact a
   secret-bearing header denylist (or run `redact_secrets` over the serialized header block) before
   persisting `headers`. *Risk: low.*
4. **Narrow the BROAD role-word match under `_` (Finding 4) — recovers real people, low effort.**
   *Risk: low–medium* (re-tune against the existing role-address tests; watch `accounts_payable@`
   class stays caught).
5. **Single-source the automation token vocabulary (Finding 7) and lowercase the dispatch frozensets
   (Finding 5) — small consistency fixes.** *Risk: low.*
6. **Same-person merge tier + provenance/confidence columns (Finding 2) — highest user-visible
   payoff, larger effort.** The deferred work; needs schema columns + HITL. *Risk: medium* (an
   over-merge is worse than a fragment — gate behind confirmation).
7. **Populate or drop `language` (Finding 6).** *Risk: low.*

> Every suggestion above is an infrastructure change that helps the next One AI install — none is a
> one-shot cleanup of this dataset, and none hardcodes an ID, domain, or threshold tuned to this org.

---

## Baseline Metrics (seed for the next audit's trend diff)

**Row counts (dev org):**

| Table | Rows |
|---|---|
| email_message | 8,386 |
| email_recipient | 18,564 |
| email_attachment | 10,092 |
| person | 1,140 |
| person_email | 1,140 |
| person_company | 847 |
| person_alias | 919 |
| company | 537 |
| company_domain | 580 |

**email_message health:** dedup_key unique 8,386/8,386 · message_id present 8,384 (2 NULL) · NULL
from_address 89 · NULL subject 54 · NULL sent_at 86 · empty body_text 249 (180 with attachments) ·
NULL from_person_id 1,084 (400 automated, 684 role/generic) · NULL direction 80 · NULL language
**8,386 (100%)** · parse_status all `parsed`.

**direction × is_automated:** inbound/auto-false 4,739 · inbound/auto-true 497 · outbound/auto-false
3,070 · null/auto-false 80.

**attachment extraction_status:** skipped_nondocument 6,119 · extracted 3,494 · unsupported_format
215 · empty 118 · scanned_pending_ocr 110 · extracted_partial_scanned 28 · truncated 6 · corrupt 1
· encrypted 1. (text-bearing total 3,528; structured grids 62.)

**masking:** 139 bodies + 23 attachments redacted, 256 markers; 0 structured-token survivals; 0
keyed-value misses; 0 FP observed.

**entity graph:** persons nameless 332/1,140 (29%) · internal persons 16 · companies 537 (1
internal, 77 with no linked person) · person:email cardinality 1:1.

**blocklist leakage:** 2,355 blocklist-only stored (2,133 trash / 190 spam / 32 drafts) = **28.1%**.

**residual duplicates:** 12 excess (all-fields-identical) / 26 excess (msgid+body) = 0.15–0.3%.

**time span:** 2023-06 → 2026-06, ramping from 2024-09, peak 2026-05 (828); no dead months in the
active period.

**source tags:** person_email/company_domain/person_alias `source` = `imap` uniformly.

---

## Queries Used (appendix — copy-paste re-runnable; `DEV='d1500000-0000-0000-0000-000000000001'`)

Dedup uniqueness:
```sql
SELECT count(*), count(DISTINCT dedup_key) FROM email_message WHERE org_id = :DEV;
```

Residual duplicates (all observable content fields identical, >1 row):
```sql
WITH copies AS (
  SELECT m.id, m.message_id, m.from_address, m.subject, m.sent_at,
         md5(coalesce(m.body_text,'')) bodyhash,
         (SELECT string_agg(lower(r.kind)||':'||lower(r.address),',' ORDER BY lower(r.kind),lower(r.address))
            FROM email_recipient r WHERE r.email_id=m.id) recipset,
         (SELECT string_agg(coalesce(a.content_hash,''),',' ORDER BY coalesce(a.content_hash,''))
            FROM email_attachment a WHERE a.email_id=m.id) atthashes
  FROM email_message m
  WHERE m.org_id = :DEV AND m.message_id IS NOT NULL AND length(trim(m.message_id))>0)
SELECT count(*) dup_groups, sum(c) rows_in_groups, sum(c-1) excess
FROM (SELECT message_id,from_address,subject,sent_at,bodyhash,recipset,coalesce(atthashes,'') ah,count(*) c
      FROM copies GROUP BY 1,2,3,4,5,6,7 HAVING count(*)>1) g;
```

Referential integrity (one representative; run the full set in the audit body for all 13):
```sql
SELECT count(*) FROM email_recipient r
WHERE NOT EXISTS (SELECT 1 FROM email_message m WHERE m.id = r.email_id);
```

Cross-org FK bleed (representative):
```sql
SELECT count(*) FROM email_attachment a JOIN email_message m ON m.id=a.email_id WHERE a.org_id<>m.org_id;
```

Masking — surviving live secrets (Postgres word-boundary regex; run per token class & per column):
```sql
SELECT count(*) FROM email_message
WHERE org_id = :DEV AND body_text ~ '\msk-(proj-|ant-|svcacct-)?[A-Za-z0-9_-]{20,}\M';
```

Masking — redaction present + per-kind tally (kinds extracted in Python from `[REDACTED:<kind>]`):
```sql
SELECT count(*) FROM email_message WHERE org_id = :DEV AND position('[REDACTED:' in body_text) > 0;
SELECT extraction_detail FROM email_attachment
WHERE org_id = :DEV AND position('secrets_redacted=' in coalesce(extraction_detail,'')) > 0;
```

Attachment contract invariant (must both be 0/empty):
```sql
SELECT count(*) FROM email_attachment WHERE org_id = :DEV
  AND extraction_status NOT IN ('extracted','truncated','extracted_partial_scanned')
  AND extracted_text IS NOT NULL AND length(trim(extracted_text)) > 0;            -- VIOLATION A
SELECT extraction_status, count(*) FROM email_attachment WHERE org_id = :DEV
  AND extraction_status IN ('extracted','truncated','extracted_partial_scanned')
  AND (extracted_text IS NULL OR length(trim(extracted_text)) = 0) GROUP BY 1;    -- VIOLATION B
```

Person fragmentation (mailbox owner):
```sql
SELECT p.id, p.is_internal, pe.email,
       (SELECT count(*) FROM email_message m WHERE m.from_person_id = p.id) sent
FROM person p JOIN person_email pe ON pe.person_id = p.id
WHERE p.org_id = :DEV AND lower(trim(p.display_name)) = 'yani lozanov' ORDER BY sent DESC;
```

Blocklist leakage (executed in Python — disk Message-ID×folder map ∩ stored `message_id`):
```text
# 1. For every .eml under the mailbox, read Message-ID + classify folder (trash/spam/junk/drafts/sent/inbox_other).
# 2. block_only = MIDs that appear ONLY in {trash,spam,junk,drafts}, never in {inbox_other,sent}.
# 3. Intersect block_only with SELECT message_id FROM email_message WHERE org_id=:DEV  (normalize <...>).
#    Result: 2,355 leaked (2,133 trash / 190 spam / 32 drafts).
```

xlsm case-mismatch reproduction:
```python
"application/vnd.ms-excel.sheet.macroenabled.12".lower() in _XLSX_CONTENT_TYPES  # -> False (bug)
```

---

## Scout Reports

No sub-scouts were dispatched: the `Agent`/team tooling returned *"Agent is not available inside
subagents"* in this execution context, so the orchestrator performed all scouting directly. The work
that the planned scout team would have done was still executed, in-line:

- **schema-scout** (FK/orphan/tenant/constraint) → Findings O-1; all 13 orphan checks + 8 cross-org
  checks + constraint dump returned clean.
- **content-scout** (body quality / masking) → Findings O-2, O-5, 3; masking precision+recall +
  markup-cleanliness confirmed; header gap surfaced.
- **entity-scout** (person/company dedup, role gating, IDN) → Findings 2, 4, 7, O-4, O-6.
- **sync-scout** (disk↔DB reconciliation, blocklist) → Finding 1 (the critical one) + Novel 1.
- **relationship-scout** (recipient/attachment/company joins) → O-3, O-4, recipient-resolution +
  attachment-flag coherence.
- **novel-scout** (free exploration) → Novel Discoveries 1–5 + Findings 5, 6, 8.
