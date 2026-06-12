# DB Data-Quality Audit — Full Database

**Date:** 2026-06-10
**Database:** One AI MVP dev Postgres (single dev org `d1500000-0000-0000-0000-000000000001`), migrations at head `0013_least_privilege_grants`. The codebase fix-pass from earlier today is **uncommitted** — code citations refer to the working tree.
**Baselines:** `docs/audits/2026-06-09_imap-data-quality.md` + `testing/11_imap-data-quality/STANDARD.md`.

## Scope

Every table in the schema, audited in two halves:

| Table | Rows (audit snapshot) | Half |
|---|---:|---|
| organizations | 2 | static (identity-data) |
| users | 4 | static (identity-data) |
| platform_admins | 1 | static (identity-data) |
| refresh_tokens | 0 | static (identity-data) |
| support_grant | 0 | static (identity-data) |
| audit_log | 0 | static (identity-data) |
| connector_connection | 1 | static (sync-ledger) + dynamic |
| connector_sync_run | 0 | static (sync-ledger) + dynamic |
| connector_sync_cursor | 0 | static (sync-ledger) + dynamic |
| email_message | 13,583 | dynamic |
| email_recipient | 27,932 | dynamic |
| email_attachment | 17,386 | dynamic |
| person | 1,154 | dynamic |
| person_email | 1,154 | dynamic |
| person_alias | 1,293 | dynamic |
| person_company | 884 | dynamic |
| company | 572 | dynamic |
| company_domain | 572 | dynamic |

Plus the schema-constraints inventory (all 19 tables incl. `alembic_version`). Ingest ledger for the corpus: **stored 13,583 + skipped 52 + failed 0 = 13,635 .eml files** on disk under `spikes/imap_dump/yani.lozanov@ethera-tech.com` — total-level reconciliation passes exactly. The static half ran **mid-ingest** (its row-count snapshots are lower); all structural findings were re-verified against the final state.

## Method

- **8 read-only hunters** (SELECT-only, zero mutations): 3 static-domain hunters (identity-data — 41 checks, sync-ledger — 27 checks, schema-constraints — 17 checks; 85 checks total) + 5 dynamic-domain hunters (email-content, recipients-attachments, entity-graph, referential-crosstenant, statistical-sanity).
- **Adversarial verification:** every candidate finding was handed to an independent verifier agent that re-ran the SQL against the live DB, byte-checked on-disk evidence in `spikes/imap_dump`, read the cited code, and actively hunted for design sources (docstrings, migrations, design docs, STANDARD.md, prior audits) that would legitimize the state. Verdicts: CONFIRMED (defect, reproduced, not legitimized), BY-DESIGN (reproduced and explicitly documented as intentional), PLAUSIBLE (reproduced; intent ambiguous). 0 findings were refuted outright; several had severity adjusted by the verifier.

**Combined tally: 48 findings** (17 static + 31 dynamic) — **30 CONFIRMED, 17 BY-DESIGN, 1 PLAUSIBLE**; **10 high, 12 medium, 26 low**. The 10 highs collapse into **6 distinct defects** (the dedup failure was independently confirmed by 4 hunters; the org-FK gap by 2).

---

## 1. Executive verdict

**The database is structurally sound but the email corpus is NOT in good shape for its purpose.** Referential integrity, tenancy isolation, and the entity-resolver guards all held under adversarial probing — zero cross-tenant mismatches, zero orphans, RLS ENABLE+FORCE everywhere, the 7c90f55 sanitizer hardening intact. But the content layer carries one systemic defect that dominates everything else, plus a contaminated entity-graph head and two structural gaps that production must not inherit.

The five things that matter most:

1. **THE DEDUP ANSWER: the ~40% cross-folder duplication is STILL STORED.** The DQ-B05 remediation (content-identity `dedup_key`) collapsed only **52 of ~5,395 duplicate copies (~1%)**. 2,537 Message-ID groups hold 7,880 rows — **5,343 redundant rows = 39.3% of email_message** (vs the STANDARD.md threshold of <0.1%), dragging 9,506 redundant recipient edges (34%) and 7,477 redundant attachment rows (43%) with them. Root cause proven at byte level: Outlook re-serializes each folder copy with fresh `----=_NextPart_...` MIME boundaries that recur **inside** the body, so `_dedup_key`'s raw-body-bytes hash fragments per copy. The docstring premise "folder copies differ ONLY by prepended trace headers" is empirically false; three docstrings now claim a fix that does not work. Until fixed, Ask retrieval returns up to 9 copies of one email and every relationship metric is inflated ~1.65x.
2. **A phantom tenant exists right now.** All 13,583 emails + the whole entity graph hang off org `d1500000-...0001`, which has **no `organizations` row** — and only `users` has an org FK, so nothing could catch it (14/15 org_id tables unguarded). GDPR erasure and org lifecycle anchor on the organizations row and can never see this tenant's PII. Dev-seam in origin, production-grade hazard in structure.
3. **The entity-graph head is contaminated.** Outlook quote-wrapping pollutes 8.8% of person display_names and 26.8% of the alias table; 13 phantom automation persons exist (one a 10-alias over-merge hub mixing 8 real humans); **abv.bg — a consumer free-mail provider — is the single largest "company" in the graph** (22 unrelated people as colleagues); IBM is split into 5 companies; the highest-degree person is nameless. Each is deterministic to fix; together they degrade every dossier and ranking view.
4. **The connector lifecycle is entirely unaudited.** A connector_connection was created today and produced zero audit rows; `AuditAction` has no `connector.*` values. Combined with cascade-on-delete, a connector DELETE would erase the corpus *and* the only record it existed (matches codebase-review H-7).
5. **The positives are real.** Tenancy/referential invariants: 100% clean (12/12 org-coherence checks = 0, 0 orphans, 0 cross-tenant rows, multi-case addresses minted zero duplicate persons). The 2026-06-09 remediations landed exactly as quantified: −73 persons (DQ-C02 gating), +77 companies (DQ-D01), person_alias 0→1,293 (DQ-K04), NULL-name rate 38%→28.8%. The pipeline's failure mode is over-storage and noise — not loss, not corruption, not leakage.

---

## 2. Findings by severity (CONFIRMED + PLAUSIBLE)

All cleanup SQL below is **PROPOSED — NOT EXECUTED**. Severities are post-verification.

### HIGH

#### H-1 — email_message (+ email_recipient, email_attachment): DQ-B05 dedup key fails on the real corpus — 39.3% duplication stored

*Consolidates 4 independently-confirmed findings (email-content, recipients-attachments, referential, statistical hunters — all CONFIRMED).*

- **Evidence:** 13,583 rows / only 8,238 distinct `message_id`. 2,537 dup groups / 5,343 redundant rows / max 9 copies; 2,410–2,418 groups byte-identical on body_text+subject+from+sent_at; all 13,583 `dedup_key` values distinct; ingest skipped only 52/13,635. 2,225/2,537 groups have differing `boundary=` in the stored Content-Type. Worst group: Message-ID `082a01dcc79f$0fc0e6e0$2f42b4a0$@ethera-tech.com` ("Демо") — 9 rows, 9 dedup_keys, 1 logical email (2 on-disk copies in INBOX.Sent + 7 in INBOX.Trash). Byte-diff of `INBOX.Sent/1708290653/5322.eml` vs `7581.eml`: identical 279,554 bytes except regenerated `----=_NextPart_...` boundary strings (+ a Thread-Index blob) — the boundaries recur through the body, so `_body_bytes()` hashes differently per copy. Downstream: 9,506/27,932 recipient rows (34%) and 7,477/17,386 attachment rows (43%, 4,817 MB of size accounting) sit on redundant copies; ~10 MB duplicated body_text; prior baseline 2,533/5,395 → the fix removed exactly the 52 ledger skips.
- **Verdict:** CONFIRMED (pipeline defect). Violates the design contract "One row per LOGICAL email — dedup by Message-ID" (`docs/connect-email-ingestion-design.md:72`) and the model invariant (`backend/app/connectors/imap/models/email.py:12-15`). Docstrings claiming the fix works are false: `email_parser.py:21`, `email.py:12-15`, plus the stale "dedups by Message-ID" in `backend/scripts/ingest_imap_dump.py:17-18`.
- **Proposed cleanup (NOT EXECUTED):**

```sql
-- Collapse folder copies that are identical on logical content
-- (content guard protects the ~8 known genuine Message-ID reuses, DQ-G01).
WITH dup AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY org_id, connection_id, message_id,
                        md5(coalesce(body_text, '')), coalesce(subject, ''),
                        coalesce(from_address, ''), sent_at
           ORDER BY created_at, id) AS rn
  FROM email_message
  WHERE message_id IS NOT NULL
)
DELETE FROM email_message WHERE id IN (SELECT id FROM dup WHERE rn > 1);
-- email_recipient / email_attachment follow via ON DELETE CASCADE.
-- Expected: ~5,3xx message rows, ~9.5k recipient rows, ~7.5k attachment rows removed.
-- The ~120 groups with non-identical bodies (quoted-printable wrap variance) need
-- a second, manual-review pass — do not blind-delete those.
```

- **Pipeline fix:** `_dedup_key` (`backend/app/connectors/imap/parsing/email_parser.py:139-166`) must hash **content identity, not raw serialization**: hash Message-ID + From/Subject/Date + the *decoded* body text + sorted attachment `content_hash`es (or strip/normalize `NextPart`-style boundary tokens before hashing the bytes). Then correct the three docstrings and re-run the ingest from the preserved disk corpus to validate (expected: ~8,240 stored). Re-running the corrected ingest is the cleaner alternative to the DELETE above.

#### H-2 — connector_connection + 12 tenant tables: phantom tenant exists; org_id has no FK anywhere except users

*Consolidates static findings 6, 7, 12 (sync-ledger + schema-constraints hunters — all CONFIRMED).*

- **Evidence:** `organizations` holds only demo (`c92e2a84-...`) and globex (`a2959841-...`); the dev ingest org `d1500000-0000-0000-0000-000000000001` is absent while the full corpus (13,583 emails, 1,154 persons, 572 companies, 1 connector_connection...) is stamped with it. FK census: exactly **1** FK references organizations in the whole DB — `users.fk_users_org_id`. 14/15 org_id-bearing tables have no DB-level org referential integrity (2 of those — audit_log, support_grant — are documented no-FK-by-design; **12 are undesigned gaps**). RLS cannot help: `org_isolation` validates org_id against the session GUC, never against the registry. `erasure_service.py:141` raises 404 when the org row is missing — this tenant's PII is unreachable by the GDPR erasure path.
- **Verdict:** CONFIRMED, high. The dev org itself is a documented dev-only seam (`ingest_imap_dump.py` "DEV driver, NOT the production sync path"; the identity-domain verifier ruled that aspect BY-DESIGN) — but the *schema gap that allowed it* is undesigned, already flagged as a hazard by the codebase review (2026-06-10 review, line 51), and exercised in the live data today.
- **Proposed cleanup (NOT EXECUTED):**

```sql
-- Make the dev tenant visible/manageable (precondition for the org-FK migration):
INSERT INTO organizations (id, slug, name, status, created_at, updated_at)
VALUES ('d1500000-0000-0000-0000-000000000001', 'dev-ingest',
        'Dev Disk-Ingest Org', 'active', now(), now());
```

- **Pipeline fix:** `backend/scripts/ingest_imap_dump.py` get-or-creates the organizations row for `--org` before writing; schema fix = item 1 of the hardening plan (§4): FK `org_id → organizations(id)` on the 12 undesigned tables.

#### H-3 — person / person_alias: Outlook single-quote-wrapped names pollute the entity graph

*Consolidates dynamic findings 8 (recipient side, medium) + 18 (person side, high) — both CONFIRMED.*

- **Evidence:** 9,215/27,932 recipient rows (33%) store `'Name'` with literal quotes (391 distinct names); **101/1,154 persons (8.8%)** have a quote-wrapped canonical `display_name`; **398/1,293 aliases (30.8%)** are quote-wrapped, of which **347 are exact duplicates of an unquoted twin** on the same person (26.8% of the alias table is quote bloat). `backfill_display_name` fills only blank names, so a quoted first sighting locks the quoted form permanently. Exceeds the DQ-F01 dirty-display_name budget (<5%). Nothing in parser (`sanitize()` = NUL-strip + cap) or resolver trims the convention.
- **Verdict:** CONFIRMED (pipeline defect, high). Raw-as-seen storage on `email_recipient.name` is by-design; the resolver — the designated normalization seam — performing none is not.
- **Proposed cleanup (NOT EXECUTED):**

```sql
-- 1. Drop the 347 quoted aliases that have an unquoted twin:
DELETE FROM person_alias a
WHERE a.alias LIKE '''%''' AND length(a.alias) > 2
  AND EXISTS (SELECT 1 FROM person_alias b
              WHERE b.person_id = a.person_id AND b.alias = btrim(a.alias, ''''));
-- 2. Unquote the remaining ~51 quoted-only aliases:
UPDATE person_alias SET alias = btrim(alias, '''')
WHERE alias LIKE '''%''' AND length(alias) > 2;
-- 3. Unquote the 101 locked canonical display_names:
UPDATE person SET display_name = btrim(display_name, '''')
WHERE display_name LIKE '''%''' AND length(display_name) > 2;
-- email_recipient.name stays raw — that column is as-seen by contract.
```

- **Pipeline fix:** strip the conventional Outlook single quotes (and re-strip whitespace) in `EntityResolver._enrich_person` (`backend/app/entities/services/entity_resolver.py:148-164`) before alias write + display_name backfill — the resolver is the documented normalization seam, the parser stays raw.

#### H-4 — person / person_alias: compound automation localparts mint phantom persons (role-guard exact-match blind spot)

- **Evidence:** 13 phantom persons for compound no-reply addresses (`drive-shares-dm-noreply@google.com`, `meetings-noreply@google.com`, `account-security-noreply@accountprotection.microsoft.com`, `invitation-do-not-reply@trello.com`, ...). The worst is an over-merge hub: 10 aliases naming 8 distinct real humans ("Alex Mungov (via Google Drive)", "Svilen Pavlov (via Google Docs)", ...), display_name "Alia Models (via Google Sheets)" — the **#2 entity in the whole graph by alias count**. 54 email_message rows attribute `from_person_id` to phantoms. `is_role_address()` (`backend/app/entities/services/address_rules.py:73`) is exact frozenset membership, so `drive-shares-dm-noreply` ≠ `noreply` passes; the DQ-C01 `allow_person=False` seam also missed (all 17 drive-shares messages stored `is_automated=false`). Exact-match violations of the guard list = 0 — the gap is purely compound localparts.
- **Verdict:** CONFIRMED (pipeline defect, high). Violates design §5 "a role/no-reply address never becomes a Person" and produces exactly the over-merge bug class the module's invariant promises to prevent.
- **Proposed cleanup (NOT EXECUTED):**

```sql
DELETE FROM person p
USING person_email pe
WHERE pe.person_id = p.id
  AND split_part(pe.email, '@', 1) ~
      '(noreply|no-reply|no_reply|donotreply|do-not-reply|notification|notify|mailer|bounce|daemon|newsletter)'
  AND lower(split_part(split_part(pe.email, '@', 1), '+', 1)) NOT IN
      ('noreply','no-reply','notifications','notification','mailer','bounce',
       'daemon','automated','auto','delivery','newsletter');
-- 13 persons; person_email/person_alias/person_company CASCADE;
-- email_message.from_person_id and email_recipient.person_id are ON DELETE SET NULL.
```

- **Pipeline fix:** change `is_role_address()` (and `_AUTOMATED_LOCALPARTS` in `flags.py`) from exact set membership to token/substring matching on the localpart (e.g. split on `-_.` and match any token, or regex `(^|[-_.])(no-?reply|donotreply|do-not-reply|notifications?|mailer|bounce|daemon)([-_.]|$)`).

#### H-5 — audit_log: connector lifecycle entirely unaudited

- **Evidence:** audit_log = 0 rows; append-only trigger present + enabled; RLS forced — guards intact, emptiness expected post-reseed. But connector_connection `7b7bd0e9-...` was created today (17:39:34Z) with **zero** audit rows: `AuditAction` (`audit_service.py:40-62`) has 20 actions, none `connector.*`; `connector_service.py` contains no audit call.
- **Verdict:** CONFIRMED, adjusted low→**high** to match the project's own calibration (codebase-review H-7: connector DELETE cascades away the corpus AND the only record it existed; actor identity discarded at the route boundary). `.claude/rules/security.md` mandates who/what/when/which-entity for consequential actions.
- **Cleanup:** none (no bad rows — missing rows can't be backfilled honestly).
- **Pipeline fix:** add a `connector.*`/`sync.*` AuditAction namespace, emit from `connector_service` create/disable/delete and SyncRunner start/finalize, plumb actor identity through the route, and add the FIX_BEFORE_PROD row (H-7/CA-CONN-06) so production cannot ship without it.

### MEDIUM

#### M-1 — users / platform_admins: email uniqueness is byte-wise only (no lower(email) guard) — CONFIRMED, schema-gap

`users_email_key` / `platform_admins_email_key` are plain `UNIQUE (email)`; the documented "real global-uniqueness guarantee" lives only in the Pydantic `NormalizedEmail` boundary. Any non-API writer (seed scripts, psql, future internal paths) can insert `Bob@x.com` beside `bob@x.com`, and the lowercased login lookup would silently never match. Current data clean (0 case-dups, 0 non-lowercase across 5 identities). **Fix:** hardening plan item 2 (functional unique index on `lower(email)`). No cleanup needed.

#### M-2 — support_grant: no DB lifecycle-coherence constraints — PLAUSIBLE, schema-gap

Only `ck_support_grant_status` exists; nothing requires approved rows to carry `expires_at`/`decided_at`/`decided_by_user_id` or requested rows to have them NULL. Mitigated by fail-closed `grant_is_active` (NULL expires_at → not active) and a single guarded write path; intent ambiguous (services-own-lifecycle is documented; omitting the DB CHECK as a tradeoff is not). Table empty — purely a permitting gap on the consent/compliance artifact. **Fix:** hardening plan item 6. No cleanup needed.

#### M-3 — connector_sync_run / connector_sync_cursor: org-coherence is app-only (no composite FK possible today) — CONFIRMED, schema-gap

FKs are on `connection_id` alone; `connector_connection` has no `UNIQUE (id, org_id)`, so a child row whose org_id differs from its connection's org would satisfy every DB constraint (FK validation bypasses RLS; the policy checks the GUC, not the parent). 0 mismatches today (tables empty). **Fix:** hardening plan item 3. No cleanup needed.

#### M-4 — audit_log / support_grant / TenantMixin: model docstrings contradict the live RLS posture — CONFIRMED, stale security documentation

`audit_log.py:9-11` still asserts the table "is deliberately NOT placed under any RLS policy" while 0013 put it under ENABLE+FORCE with `org_isolation` USING+WITH CHECK; `base_model.py` TenantMixin says "RLS once policies land" (landed in 0009/0011/0013); `support_grant.py:11` says "inert today" (enforced). `FIX_BEFORE_PROD.md:80` carries the superseded wording too. Under rule A4 docstrings are runtime input — a security invariant asserting the opposite of the enforced posture on the compliance-critical table is a real defect. (Caveat: a violating INSERT fails loudly, not silently.) **Fix:** correct the three docstrings + the FIX_BEFORE_PROD line. No cleanup needed.

#### M-5 — email_message: mojibake — 41 rows (0.30%) with U+FFFD in body_text, 3x over DQ-F02 — CONFIRMED, data-defect

Confined to bodies whose declared charset fails to decode (`_decode_text_part` `errors='replace'`); subjects clean; 0 classic UTF-8-as-latin-1 digraphs. Verified end-to-end: the kambourov sample declares gb2312 but contains the cp1252 euro byte 0x80 — sender-side mislabeling, parser degrades as documented. 41 rows = 24 distinct bodies from 7 senders. Related sender-side damage (not ours): 9 all-`???` subjects.
**Cleanup (NOT EXECUTED):** no SQL repair — the information is lost in the stored text. Identification: `SELECT id FROM email_message WHERE body_text LIKE '%'||chr(65533)||'%';` Repair = re-ingest those messages after the pipeline fix.
**Pipeline fix:** add a charset-fallback chain in `_decode_text_part` (`email_parser.py:247-260`): on UnicodeDecodeError try cp1252 → windows-1251 before `errors='replace'` (recovers the known Outlook gb2312/cp1252 quirk and most Cyrillic cases).

#### M-6 — email_recipient: within-message duplicate edges — 183 groups / 199 redundant rows, unchanged since DQ-B06 — CONFIRMED, pipeline-defect

Parser appends every `getaddresses()` occurrence; no per-message dedup; no UNIQUE on (email_id, kind, address). All copies resolve to the same person (0 mis-links) — pure edge-weight inflation. Same count as the prior audit, i.e. unfixed.
**Cleanup (NOT EXECUTED):**

```sql
DELETE FROM email_recipient r USING (
  SELECT id, row_number() OVER (PARTITION BY email_id, kind, address ORDER BY id) AS rn
  FROM email_recipient) d
WHERE r.id = d.id AND d.rn > 1;   -- 199 rows
```

**Pipeline fix:** dedup recipients per (kind, address) inside `_extract_recipients` (`email_parser.py:303-317`); then guard with the UNIQUE constraint (hardening plan item 4).

#### M-7 — company / person_company: Bulgarian free-mail domains minted as companies; abv.bg is the #1 company in the graph — CONFIRMED, pipeline-defect

`_GENERIC_DOMAINS` (51 domains, DACH/global) misses BG providers: abv.bg minted as a Company with **22 unrelated people** (tied rank #1 of 572, ahead of every real counterparty), mail.bg with 1; 23 bogus person_company links. Exactly the "bogus shared Company" the module's docstring promises to prevent; breaks DQ-K03 "no junk head".
**Cleanup (NOT EXECUTED):**

```sql
DELETE FROM company c USING company_domain cd
WHERE cd.company_id = c.id AND cd.domain IN ('abv.bg', 'mail.bg');
-- company_domain + person_company (23 links) CASCADE.
```

**Pipeline fix:** extend `_GENERIC_DOMAINS` (`address_rules.py`) with the Bulgarian free-mail set (`abv.bg`, `mail.bg`, `dir.bg`, `inbox.bg`, ...) — the docstring explicitly anticipates list extension. BG corpora are production-plausible (CON-09/CON-10 roadmap).

#### M-8 — company_domain / person_email: IDN-homoglyph domain split (breeze.nо → breeze.xn--n-1tb) — CONFIRMED, data-defect

The Cyrillic-о homoglyph of breeze.no arrived punycoded on the wire and minted a separate company + a duplicate person (Mariusz Przybylski split in two). DQ-B04 target is 0 punycode pairs; the pipeline has no IDN awareness. Security-relevant (homoglyphs are a spoofing pattern). One pair / 8 messages — localized.
**Cleanup:** none safe — the domains are genuinely distinct strings; a naive merge would be the WRONG fix. **Pipeline fix:** detect `xn--` / mixed-script domains at resolve time and quarantine/flag instead of minting first-class entities; surface for human review (HiTL).

#### M-9 — company / company_domain: subdomain company fragmentation — 37 pairs (3x the prior audit's 12) — CONFIRMED

37 stored domains are subdomains of another stored company's domain: IBM fractured into 5 companies (bg.ibm.com 12 people **outranks** ibm.com 9), 6 *.atlassian.net SaaS tenants, 6 *.hostinger.com marketing subs. `_observe_company` keys on the full host with no registrable-domain folding. The prior audit already recorded remediation #5 "reduce company key to eTLD+1".
**Cleanup:** company-merge script driven by an eTLD+1 (public-suffix) library — not mechanical SQL; defer to the pipeline fix. **Pipeline fix:** fold the company key to eTLD+1 via the public-suffix list in `entity_resolver._get_or_create_company`, keeping full-host rows in company_domain as evidence.

#### M-10 — person: 332/1,154 persons (28.8%) have NULL display_name, including the graph's highest-degree head — CONFIRMED

lazarina.paneva@apis.bg (188 touches), artem.kalmakov@ethera-tech.com (75), thegatekeeperpoc@gmail.com (60) are nameless. Verified NOT a pipeline bug: zero non-blank source names exist anywhere for all 332 (address-only To/Cc entries) — the DQ-K04 backfill works (rate improved 38% → 28.8%). Fails STANDARD.md DQ-K04 green ("low; none in top-degree") on both axes.
**Cleanup:** none honest — no source names to repair from. **Pipeline fix:** an enrichment tier (derive display candidates from the localpart, e.g. `lazarina.paneva` → "Lazarina Paneva", clearly marked as derived; or defer to the future name-merge tier) — a product decision, not a data repair.

### LOW (CONFIRMED)

| # | Table(s) | Finding | Evidence | Cleanup / fix |
|---|---|---|---|---|
| L-1 | organizations | Slug format unenforced in DB (pattern only in Pydantic); slug is the erasure confirmation token | 0/2 current slugs violate; no CHECK exists | Hardening item 6 (`ck_organizations_slug_format`) |
| L-2 | connector_sync_run | Ledger accounting contract (seen = stored+skipped+failed, finished_at ≥ started_at, counts ≥ 0) has zero DB enforcement | All checks 0 (table empty); only the status CHECK exists | Hardening item 5 |
| L-3 | connector_sync_cursor | Cursor invariants app-only: nullable uidvalidity with no >0 CHECK (NULL coalesced to 0 in `_FolderTracker`), no failed_uids element/cap checks; model docstring says advance "steps OVER" failed UIDs but code stops AT — contract ambiguity | All checks 0 (table empty); `uq_sync_cursor_identity` exists and is good | Hardening item 5 + fix the docstring |
| L-4 | connector_sync_run | `run_id` (the fencing token) has no UNIQUE and no index; `finalize()` would silently finalize duplicates | 0 dups (empty); sibling cursor table got its UNIQUE in the same migration | Hardening item 5 (`UNIQUE (org_id, run_id)`) |
| L-5 | person_email / person_alias / company_domain | The three provenance `source` columns are the only enum-like text columns with no CHECK (13 CHECKs exist elsewhere) | All current values 'imap' | Hardening item 6 |
| L-6 | email_message | C0 control chars survive into body_text: 19 rows with U+0007/U+000B (only NUL is stripped) | Subjects clean; will flow into chunking/embedding | Cleanup: `UPDATE email_message SET body_text = regexp_replace(body_text, '[\x01-\x08\x0B\x0C\x0E-\x1F]', '', 'g') WHERE body_text ~ '[\x01-\x08\x0B\x0C\x0E-\x1F]';` (19 rows, NOT EXECUTED). Pipeline: extend `_extract_body_text` sanitization to all C0 except `\t\n` |
| L-7 | email_message | 9 rows with `from_address='System Administrator'` (Exchange NDR display token, no @); also stored `is_automated=false` (localpart check requires @) | All 9 are "Undeliverable:" NDRs; person_email clean (0 junk persons) | Cleanup: `UPDATE email_message SET from_address = NULL WHERE from_address IS NOT NULL AND from_address NOT LIKE '%@%';` (9 rows, NOT EXECUTED). Pipeline: addr-spec shape check before storing from_address; classify NDLs as automated |
| L-8 | email_attachment | Hygiene composite: 1 filename stored as `''` vs 10 NULL (sanitize() passes `''` through — inconsistent absent-value encoding); 3 zero-byte files (honest); 9 extracted_text mojibake (= prior DQ-H02, documented) | Counts exact | Cleanup: `UPDATE email_attachment SET filename = NULL WHERE filename = '';` (1 row, NOT EXECUTED). Pipeline: coalesce `'' → None` at `email_parser.py:280` |
| L-9 | email_recipient | Uncapped-recipients hazard did NOT materialize (p50=1, p99=10, max=43, all human correspondence) — but `_store_recipients` remains uncapped; residual exposure for a future bulk mailbox | Already tracked in the codebase-review audit | Pipeline: per-email recipient cap or bulk-mail person-minting guard; no cleanup |
| L-10 | email_message + all children | Structural gap: every child→parent FK is single-column, so the DB does not enforce child.org_id = parent.org_id; erasure hooks run on the RLS-exempt session where the per-statement org filter is "the ONLY containment" | All 12 child-vs-parent org checks = 0 today; RLS forced everywhere | Hardening item 3 (composite FKs); no cleanup |

---

## 3. By-design registry (verified intentional — stop re-flagging these)

Each state below was adversarially verified as *documented* design. Future audits should treat them as accepted unless the cited source changes.

| # | Table / state | Why it's by-design (source) |
|---|---|---|
| B-1 | Dev org `d1500000-...` minted by the disk driver without lifecycle integration (the org-row absence itself is fixed by H-2's INSERT) | `ingest_imap_dump.py:2-4` "DEV driver (NOT the production sync path)"; fixed recognizable dev id documented at lines 39-41; fail-closes outside local/test |
| B-2 | refresh_tokens: polymorphic subject_id with no FK | `refresh_token.py:8-10` "users and platform_admins live in separate tables, so no FK"; erasure deletes tokens FIRST (documented ordering invariant) |
| B-3 | Active disk ingest invisible to the sync ledger (sync_status='idle', 0 run/cursor rows, synced_count=0 beside 13,583 emails) | Ledger tables belong to the SyncRunner path (migration 0011 docstring); the disk driver bypasses it by construction. Decision needed before prod: first real IMAP sync starts cursorless and re-fetches everything, leaning on the dedup key — must not happen before H-1 is fixed |
| B-4 | users.email globally unique (not per-org) — with a cross-tenant existence-leak side effect on create (409) | Documented MVP tradeoff in 4 places (`user.py:10` "MVP: one user = one org", service/exception/migration). Revisit before multi-org contractors |
| B-5 | audit_log + support_grant: no org FK | Durable compliance attribution must survive org deletion (`audit_log.py:12`, `support_grant.py:12`, migration 0006) |
| B-6 | 80 email_message rows with from/direction/received_at all NULL | Unsent Outlook drafts (21 in Drafts + 59 deleted drafts in Trash, verified on disk: no From/Date headers exist); `derive_direction` docstring: "never guess a direction we can't justify". Carry forward as an Ask-retrieval requirement (undated/unattributed docs must still be reachable) |
| B-7 | Full Bcc lists retained in headers JSONB (71 messages; the real exposure is 37 outbound copies) | Full-header retention is the documented no-raw-bytes hedge (`design.md:70`); kind='bcc' recipient rows intentional; access-control-below-org_id tracked separately. **Privacy seam for Ask:** treat `headers->'Bcc'` as restricted |
| B-8 | email_attachment content-level duplication (70.9% rows / 75.8% of size_bytes are duplicate blobs by sha256) | Lean-attachments design: bytes discarded, one metadata row per occurrence (`design.md:77-80`); actual table is 10 MB. Sizes the future blob store + mandates hash-keyed extraction caching and a content_hash index when CA-CONN-04 lands |
| B-9 | CA-CONN-04: 17,045 binary attachment rows (98%, 9.86 GB) have extracted_text NULL; text-like path works at 99.1% | Explicitly deferred seam with a hard production gate (`attachment_extractor.py` docstring + `docs/FIX_BEFORE_PROD.md` CA-CONN-04); corpus re-extractable from disk. Census update: 2,306 PDF / 1,641 docx / 99 xlsx / 68 pptx / 61 doc ≈ 4,175 business-doc rows invisible to retrieval |
| B-10 | 51 email_recipient rows with non-addr-spec tokens ('mailto' x28, 'undisclosed-recipients', ...) | As-seen layer-1 fidelity (resolver docstring enumerates the garbage-address no-person case; STANDARD.md DQ-K02 bucket (b)); containment exact: 0 junk persons |
| B-11 | 3 mojibake aliases + cross-identity junk aliases on the mailbox owner | Sender-side destruction inside RFC2047 encoded-words (verified on wire bytes); record-every-sighting is the DQ-K04 mandate. Future name-merge must filter junk at consumption time |
| B-12 | 15 persons with their own address as display_name | Header-faithful; DQ-F01 anticipates "==address" within <5% budget (1.3%). Improvement noted: treat address-shaped names as blank in the backfill predicate |
| B-13 | 1 person (nelly.galabova@bright-mr.com) with fully NULL seen window | Her only sighting is a date-less message; window is driven by received_at and NULL = "date unknown" is the provenance-honest representation (fabricating from ingest time would be false data) |
| B-14 | 8 case-variant alias pairs ('poojitha'/'Poojitha', Cyrillic/Latin renderings) | UNIQUE is deliberately byte-exact for idempotency (migration 0012 docstring); script/case variants are genuine as-seen signal; read-side normalization is the consumer's call |
| B-15 | connector_connection sync-state columns all default beside a full corpus | Same as B-3 — the sync_* columns are owned by the never-run SyncRunner path |
| B-16 | Ingest run ledger not persisted for the disk ingest (reconciliation only vs stdout/disk: 13,583+52=13,635 ✓) | Dev-driver scope (B-1/B-3); decision recorded: production ingest must persist sync runs before go-live |
| B-17 | Entity-density drift vs 2026-06-09 (persons −73, companies +77, aliases 0→1,293) | Each delta equals a quantified landed remediation (DQ-C02 / DQ-D01 / DQ-K04) cited in the resolver's own docstrings — expected movement, baselines are stale, not regressed |

Also re-verified clean (positive regression floor, dynamic verification finding): attachment filename sanitization (0 path separators/control chars/traversal), content_hash 100% `^[0-9a-f]{64}$` with 0 size mismatches, content_type 100% valid, 0 orphans / 0 out-of-org rows / 0 dangling person_id anywhere, DQ-C02 reply_to/sender 100% person-NULL, multi-case addresses → 0 duplicate persons (normalize_email held), is_inline ⇒ content_id.

---

## 4. Schema-hardening plan (proposed migration `0014_data_quality_guards` — sketch, NOT applied)

Ordered by value. Items 1–3 close the gaps behind H-2/M-3/L-10; 4–6 make the app-only invariants self-defending.

```python
"""0014_data_quality_guards — close the org-integrity and ledger guard gaps.

Preconditions: the dev org row exists (H-2 cleanup INSERT); the email_recipient
within-message dedup (M-6 cleanup) has run.
"""

TENANT_FK_TABLES = [  # the 12 undesigned gaps; audit_log + support_grant stay FK-free by design
    "company", "company_domain", "connector_connection", "connector_sync_cursor",
    "connector_sync_run", "email_attachment", "email_message", "email_recipient",
    "person", "person_alias", "person_company", "person_email",
]

def upgrade() -> None:
    # 1. Tenant-root FKs — no more phantom tenants (H-2).
    #    Default NO ACTION on delete: erasure hooks already delete children before
    #    the org row, and accidental org deletion must not cascade tenant data away.
    for table in TENANT_FK_TABLES:
        op.create_foreign_key(f"fk_{table}_org_id", table, "organizations",
                              ["org_id"], ["id"])

    # 2. Case-insensitive identity uniqueness (M-1) — DB-level backing for the
    #    Pydantic NormalizedEmail guarantee.
    op.create_index("uq_users_email_lower", "users",
                    [sa.text("lower(email)")], unique=True)
    op.create_index("uq_platform_admins_email_lower", "platform_admins",
                    [sa.text("lower(email)")], unique=True)

    # 3. Composite tenant-coherent FKs (M-3, L-10) — child.org_id must equal the
    #    parent's org at the DB layer. Parents first get UNIQUE (org_id, id)
    #    (cheap: id is already the PK).
    for parent in ("email_message", "person", "company", "connector_connection"):
        op.create_unique_constraint(f"uq_{parent}_org_row", parent, ["org_id", "id"])
    # then per child, e.g.:
    #   email_recipient/email_attachment (org_id, email_id)  -> email_message(org_id, id)  CASCADE
    #   person_email/person_alias/person_company (org_id, person_id) -> person(org_id, id) CASCADE
    #   company_domain/person_company (org_id, company_id)   -> company(org_id, id)        CASCADE
    #   email_message/sync_run/sync_cursor (org_id, connection_id)
    #                                                -> connector_connection(org_id, id)   CASCADE
    #   email_message (org_id, from_person_id) / email_recipient (org_id, person_id)
    #                                                -> person(org_id, id)                 SET NULL
    # (replacing the existing single-column FKs; same ON DELETE semantics throughout)

    # 4. Recipient edge uniqueness (M-6) — requires the cleanup DELETE first.
    op.create_unique_constraint("uq_email_recipient_edge", "email_recipient",
                                ["email_id", "kind", "address"])

    # 5. Sync-ledger self-defense (L-2, L-3, L-4) — the "audit never lies" tables.
    op.create_unique_constraint("uq_sync_run_fencing", "connector_sync_run",
                                ["org_id", "run_id"])
    op.create_check_constraint("ck_sync_run_time_order", "connector_sync_run",
        "finished_at IS NULL OR finished_at >= started_at")
    op.create_check_constraint("ck_sync_run_terminal_finished", "connector_sync_run",
        "(status = 'running') = (finished_at IS NULL)")
    op.create_check_constraint("ck_sync_run_counts_nonneg", "connector_sync_run",
        "messages_seen >= 0 AND messages_stored >= 0 AND "
        "messages_skipped >= 0 AND messages_failed >= 0")
    op.create_check_constraint("ck_sync_cursor_uidvalidity_positive",
        "connector_sync_cursor", "uidvalidity IS NULL OR uidvalidity > 0")

    # 6. Value-shape CHECKs (L-1, L-5, L-8, M-2 + refresh_tokens hygiene).
    op.create_check_constraint("ck_organizations_slug_format", "organizations",
        "slug ~ '^[a-z0-9][a-z0-9-]*$'")
    for table in ("person_email", "person_alias", "company_domain"):
        op.create_check_constraint(f"ck_{table}_source", table,
            "source IS NULL OR source IN ('imap')")  # extend with each new connector
    op.create_check_constraint("ck_refresh_tokens_hash_shape", "refresh_tokens",
        "token_hash ~ '^[0-9a-f]{64}$'")
    op.create_check_constraint("ck_email_attachment_filename_nonempty",
        "email_attachment", "filename IS NULL OR filename <> ''")
    op.create_check_constraint("ck_support_grant_lifecycle", "support_grant",
        "((status = 'requested') = (decided_at IS NULL)) AND "
        "(status <> 'approved' OR (expires_at IS NOT NULL "
        " AND decided_by_user_id IS NOT NULL)) AND "
        "(status <> 'denied' OR decided_by_user_id IS NOT NULL)")

    # 7. DEFERRED (do with CA-CONN-04, not now): index on email_attachment(content_hash)
    #    to support hash-keyed extraction caching / blob-store dedup.
```

Not included on purpose: the ledger seen-sum CHECK (`messages_seen = stored+skipped+failed`) — abandoned rows legitimately keep 0-defaults while seen may be >0 mid-crash; enforce in the runner's finalize tests instead.

---

## 5. Comparison vs the 2026-06-09 audit (where baselines exist)

| Metric | 2026-06-09 baseline | 2026-06-10 actual | Δ / reading |
|---|---|---|---|
| Duplicate message groups / redundant rows (DQ-B05) | 2,533 / 5,395 (raw-byte key) | 2,537 / 5,343 (content key) | **Fix removed only 52 copies (~1%) — FAILED**; threshold <0.1%, actual 39.3% |
| Ingest dedup skips | — | 52 / 13,635 (0.4%) | the only copies that were byte-identical |
| person | 1,227 | 1,154 | −73 = exactly DQ-C02 routing identities now gated ✓ |
| company | 495 | 572 | +77 = exactly DQ-D01 role-suppressed domains now observed ✓ |
| person_alias | 0 (dead table, DQ-I02) | 1,293 | DQ-K04 alias writing landed ✓ |
| NULL display_name | 38% | 28.8% (332/1,154) | improved, but graph head still nameless — DQ-K04 red |
| Persons / companies per 1,000 emails | 90 / 36 | 84 / 42 | expected drift from the two remediations |
| Subdomain company pairs (DQ-B03) | 12 | 37 | **tripled**; eTLD+1 folding (prior fix #5) still missing |
| Within-message dup recipient edges (DQ-B06) | 183 | 183 (199 redundant rows) | **unfixed** |
| Attachment extracted_text mojibake (DQ-H02) | 9 | 9 | unchanged, documented limitation |
| Address-as-display-name persons (DQ-F01 bucket) | 8 | 15 | grown; still inside the <5% budget |
| Body mojibake (DQ-F02, <0.1%) | tracked | 41 rows = 0.30% | 3x over threshold (new measurement) |
| NULL-direction draft rows | 80 | 80 | unchanged, by-design (B-6) |
| Attachment content-type census | 9,109 png / 2,355 tnef / 2,306 pdf / 1,641 docx | identical byte-for-byte | same corpus, deterministic parse ✓ |
| Punycode/IDN pairs (DQ-B04, target 0) | new metric | 1 pair (breeze.no) | quarantine mechanism needed |
| CA-CONN-04 business-doc census | 281 PDF / 180 docx (1,500-email sample) | 2,306 PDF / 1,641 docx (full corpus) | ~8x the sample baseline; same 0% binary extraction |

New-in-this-audit metrics with no baseline (record as baselines for next pass): quote-wrapped names (9,215 recipient rows / 101 persons / 398 aliases / 347 dup pairs), phantom automation persons (13), free-mail companies (abv.bg 22 + mail.bg 1), C0-control bodies (19), non-addr-spec from_address (9), zero org-FK coverage outside users (1/15), audit_log connector coverage (0 actions).

---

## 6. Recommended sequence

1. Fix `_dedup_key` (H-1 pipeline fix) + correct the three false docstrings → wipe + re-ingest from disk (cleaner than the DELETE) → expect ~8,240 stored.
2. Land the resolver fixes in the same pass (H-3 quote-strip, H-4 role-token matching, M-7 BG domains, M-5 charset fallback, M-6 recipient dedup, L-6/L-7/L-8 sanitization) — one re-ingest validates all of them.
3. Provision the dev org row (H-2 cleanup), then apply migration 0014 (§4).
4. Add connector audit actions (H-5) before any production connector work.
5. Update STANDARD.md baselines from §5's right-hand column.

---

## 7. Verification — 2026-06-11 fix pass + re-ingest (measured)

All pipeline fixes, migration `0014_data_quality_guards`, and the connector audit events were implemented on 2026-06-11 (5-fixer orchestrated pass + adversarial review + 2 review-fixup iterations), the connect/entity tables were truncated, and the full 13,635-file corpus was re-ingested through the fixed pipeline. Measurements below are SELECT-only re-runs of this audit's own checks.

**Verdict: every confirmed defect is fixed or reduced to a documented residual; all structural guards are live; zero integrity regressions.** The dedup fix took three key iterations — content identity (v1), + HTML-alternative digest (v2, review catch), + UTC-instant Date and TNEF presence-marker (v3, after measurement classified the survivors: Outlook re-renders the `Date` header in a different timezone per folder copy [649 groups] and regenerates the `winmail.dat` TNEF container per copy [253 groups, 100% TNEF-correlated]).

### Before / after

| Metric | Audit baseline | Target | Measured (v3 re-ingest) | Verdict |
|---|---|---|---|---|
| Ingest ledger | 13,583 stored / 52 skipped | ~8,300 stored | **8,386 stored / 5,249 skipped / 0 failed** | ✅ |
| Content-identical redundant rows | 5,343 (39.3%) | <0.1% | **18 rows in 16 groups (0.21%)** | ✅ 99.66% reduction (residual documented below) |
| Same-Message-ID multi-row groups | 2,537 | — | 144 (120 = genuinely different content, by design) | ✅ |
| Quote-wrapped person names / aliases | 101 / 398 | 0 / 0 | **0 / 0** | ✅ |
| Phantom automation persons | 13 (incl. 10-alias hub) | 0 | **0** (1 benign `(via Google …)` alias string remains on a real person) | ✅ |
| Free-mail companies (abv.bg, mail.bg, …) | 23 links, abv.bg = #1 company | 0 | **0** | ✅ |
| IBM company fragments | 5 | 1 | **1** | ✅ |
| IDN/punycode company domains | 1 | 0 | **0** | ✅ |
| Duplicate aliases per person | 347 | 0 | **0** | ✅ |
| Within-message duplicate recipient edges | 199 | 0 | **0** (+ DB-impossible via `uq_email_recipient_edge`) | ✅ |
| C0-control bodies / junk from_address / `''` filenames | 19 / 9 / 1 | 0 | **0 / 0 / 0** | ✅ |
| Body mojibake (U+FFFD) | 41 (0.30%) | <0.1% | **15 (0.18%)** — remainder genuinely undecodable | 🟡 63% reduction |
| Phantom tenant | corpus under nonexistent org | org row + FKs | **`dev-ingest` org row exists; 13 org-FKs live** | ✅ |
| Org-coherence mismatches (child vs parent) | 0 (app-enforced) | 0 (DB-enforced) | **0, now composite-FK-enforced** | ✅ |
| Rows outside the dev org | 0 | 0 | **0** | ✅ |
| Persons / companies | 1,154 / 572 | — | 1,140 / 537 (−13 phantoms −1 split person; −35 eTLD+1 folds + free-mail removals) | ✅ deltas explained |
| NULL display_name | 332 (28.8%) | no fix shipped | 332 (29.1%) | ➖ unchanged, tracked (M-10) |
| Recipients / attachments | 27,932 / 17,386 | proportional drop | 18,564 / 10,092 | ✅ |
| Schema guards (`0014`) | none | per §4 plan | 13 org-FKs, composite tenant FKs (CASCADE/SET NULL preserved), `lower(email)` uniques, recipient-edge unique, ledger + value CHECKs, RLS ENABLE+FORCE incl. audit_log | ✅ catalog-verified |
| Connector audit events | 0 actions | namespace + emission | `connector.created/disabled/enabled/deleted`, `sync.started/finished` — emitted in service + runner, content-blind, tested. (audit_log has 0 connector rows: the disk driver bypasses the service BY DESIGN; unit tests are the enforcement) | ✅ code-verified |

### Residual duplication (18 rows, 0.21%) — documented, not hidden

The 16 surviving content-identical groups are sub-second edge cases: TNEF emails whose copies also vary in another regenerated property, and a handful of docx-bearing copies with identical attachment hashes whose remaining variance sits in the html alternative. At 18 rows against a 5,343-row baseline, further key surgery has worse risk/benefit than the residual itself (every additional exclusion widens the over-dedup surface). Recorded as the new baseline; revisit only if the rate grows on a future corpus.

### Dedup key v3 — the recipe of record

`sha256` over: normalized Message-ID + decoded From/Subject + **UTC-normalized send instant** + decoded body_text + decoded text/html digest + sorted attachment identities (**content hashes; volatile `application/ms-tnef` contributes a stable presence marker**). Headers-only and Message-ID-less messages use the injective raw-byte fallback. Hash input encoded with `surrogateescape` (injective on undecodable header bytes). Over-dedup guards test-asserted: differing send instant, body, html, or any real attachment splits the key.

### 7.1 Cross-vendor (GPT/Codex) review pass — 2026-06-11

An independent GPT-family review of the full diff returned DO-NOT-SHIP with 2 HIGH + 3 MEDIUM findings; after source-level reconciliation (3 CONFIRMED, 2 QUALIFIED — none refuted) all five were fixed, two with corrected approaches: the dedup key's TNEF handling became an **interior digest** (embedded-attachment bytes only — empirically stable across all 271 multi-copy TNEF groups, unlike raw blobs or interiors-with-RTF-body) closing the marker's silent-loss class, and the recipient envelope joined the key **excluding Bcc** (the Sent copy carries it; Codex's as-stated fix would have split every Bcc'd email's copies). Also fixed: UIDVALIDITY-0 folder skips are now loud (WARNING per sync; ledger surfacing tracked as CA-CONN-07), erasure fails closed on PARTIAL hook registries (`REQUIRED_ERASURE_HOOKS`), and person internality folds to eTLD+1 consistently with company identity. **Key recipe of record is now v4** (v3 + canonical To/Cc envelope + filename digests + TNEF interior digests). Re-verified end-to-end: suite 631 passed / 94.12% coverage; re-ingest reproduced v3's numbers exactly (8,386 stored / 5,249 skipped / 0 failed; residual 18 rows = 0.21%) — the added discrimination lost zero folds. Raw review + reconciliation: `testing/codex review/2026-06-11_full-fix-pass-review.md`.

### 7.2 Extraction-quality pass — 2026-06-11 (Phase A PDF)

First audit of the **content** of `extracted_text` (everything above audited structure and identity; this pass reads what the Phase A extractors actually stored). Method mirrors the main audit: 3 read-only hunters (text-content-quality — 14 checks; classification spot-check — 6 checks; standing-metrics regression — 26 checks), every finding re-run by an independent adversarial verifier against the live DB + byte-level payload recovery from `spikes/imap_dump` (sha256(decoded MIME part) == `content_hash`) + source/design reconciliation. **16 findings: 15 CONFIRMED, 1 BY-DESIGN, 0 refuted**; the verifier corrected two root-cause diagnoses (recorded below — the corrections change the fixes, not the verdicts). Population: **1,178 text-bearing rows / 27,027,031 chars** = 965 pdfplumber 0.11.9 (932 extracted + 28 partial_scanned + 5 truncated) + 213 text-decode v2.

**Verdict: the PDF text layer is a trustworthy retrieval substrate; the corpus as a whole is trustworthy with ~5% named noise and ONE lying status that loses high-value documents.** Everything structural holds at 100%: sanitizer guarantees (0 lone surrogates / 0 C0 / 0 CR over 27.0M chars), page-marker integrity (965/965, all 1..N monotonic), zero whitespace husks, zero silent extraction loss (0/8 re-extractions under threshold, ratios 86–103%), complete extractor provenance, and 12/12 seeded legibility samples read as real prose with live pipe-serialized tables in 44% of PDFs. The defects are enumerable, not systemic: ~564K chars of mojibake confined to 9 text-decode rows (a known seam, DQ-H02), 44 raw-markup rows, 2 unreadable PDFs out of 932. The exception that breaks trust is classification, not extraction: **`empty` is false for 91% of its PDFs** — 31/34 carry visible content (vector-glyph payroll/VAT/invoice print-outs + sub-floor scans) and the status excludes them from every OCR/recovery path forever, while the live sync path discards their bytes at parse. Separately, **live API credentials sit verbatim in the queryable column**. Retrieval over what is stored: yes. Completeness of the record and Ask-readiness of the column: not until EQ-1/EQ-2 land.

#### Measured quality table

| Check | Expectation | Measured | Verdict |
|---|---|---|---|
| C1a — U+FFFD encoding garbage | <0.5% rows | 13/1,178 rows (1.10%) / 568,310 chars. Split: text-decode 9/213 (4.2%) = 564K chars in 5 legacy files; PDF 4/965 (0.41%) = 7 chars total | ❌ corpus / ✅ pdf.py contract |
| C1b/c — lone surrogates / C0 controls / CR | 0 / 0 / 0 (MUST) | **0 / 0 / 0** over 27.0M chars (bodies cross-checked: 0/0/0 over 8,137 rows / 24.7M chars) | ✅ sanitizer guarantee holds |
| C2 — `(cid:NNN)` font junk | 0 unreadable rows | 21 rows carry cid tokens: 1 catastrophic (98.7% cid — 24,826/25,154 chars, status `extracted`) + 1 silent all-Cyrillic-glyph-drop row (488 chars of bare punctuation); 20 rows at 0.2–11.4% glyph drops; `?`-runs ≥10: 0 | 🟡 2 unreadable rows of 932 |
| C3 — whitespace husks | 0 | **0** (<30 chars / >90% ws / <20 content chars beyond markers all zero; min PDF text = 309 real chars) | ✅ |
| C4 — structure sanity | all PDF rows | 965/965 start `[page 1]`; 0 non-monotonic / non-1..N marker sequences; pipe tables alive in 421/965 (43.6%); 213/213 text-decode rows marker-free by design | ✅ |
| C5 — legibility (12 seeded samples + run-together proxy) | prose | 12/12 PDF samples legible (BG financials, bank statements, contracts); avg-word-len >15 = 1/965 (the C2 cid row). **But 44/213 text-decode rows (20.7%) are raw HTML (42) / RTF (2) source stored as `extracted`** | ✅ PDFs / ❌ HTML+RTF attachments |
| C6 — repeated-line noise | — | 116/1,178 rows (9.8%) have one line ≥10×; top offenders are per-page headers/footers (×499, ×250, ×218); text-decode hits are mostly legitimate structural repeats (XML/CSV/JSON) | 🟡 de-headering = chunking-time work |
| C7 — truncated rows | sane bail | 5/5 page-bounded at exactly MAX_PDF_PAGES=500, non-empty, under the 2M cap, end mid-document as designed. **But `ExtractionResult.detail` is persisted NOWHERE** (no DB column, never logged) | ✅ text / 🟡 detail dropped |
| C8 — extractor provenance | complete | 1,178/1,178 text rows carry name+version; pypdf won 0 rows; only NULL-provenance attempted rows = 2 honest zero-byte empties | ✅ |
| Spot 1 — `scanned_pending_ocr` (73 rows) | truly text-free | 12/12 probed docs: 0 chars from BOTH engines (text+tables), every doc has page-covering images | ✅ 0 misclassifications |
| Spot 2 — `extracted_partial_scanned` (28 rows) | text + image-only pages | 6/6: stored md5 == independent re-extraction (no loss) AND ≥1 recomputed image-only page | ✅ |
| Spot 3 — `empty` PDFs (37 rows / 34 docs) | genuinely no content | **31/34 (91%) visibly carry content**: 30 vector-text (glyphs as curves, 808–122,005 vector objects, 0 chars both engines) + 1 inset scan (image at 0.45 page-area < 0.8 floor); 3 remaining are 150–176 tiny strip images (plausibly scans → possibly 34/34). Rasterized proof: `Vedomost_202603.pdf` renders a complete payroll sheet | ❌ HIGH — lying terminal status |
| Spot 4 — silent extraction loss (8 re-extractions) | stored ≥ re-extracted | 0/8 under the 50% threshold (ratios 86–103%); 1 broken-CMap garbage row found (signed BG contract, ~0.1% prevalence) | ✅ loss / 🟡 1 garbage row |
| Spot 5 — `unsupported_format` census (2,683 rows / 2,457 hashes) | no hidden PDFs | **6 real PDFs (verified %PDF magic, 23.3 MB) under `application/octet-stream`** (design §2.10 estimated ~2); rest exact: TNEF 1,560 (all magic-verified), docx 845, octet-stream 102, xlsx 63, msword 39, pptx 30, …; 0 alias `*pdf*` content types; 1 nested-multipart hash unverifiable | 🟡 Phase B sniff rescues |
| Spot 6 — `skipped_nondocument` (6,119 rows) | nondocs only | Composition exact: images 5,996, archives 59, audio 30, video 1, delivery-status 17, signatures 16; 0 rows with document extensions | ✅ |
| Standing metrics (§7 + §7.1) | no regression | All reproduce exactly: 8,386 stored / residual 18 rows in 16 groups (0.21%) / body mojibake 15 / same-Message-ID groups 144 / alias exact-dups 0 / word_count recompute 0 mismatches on all 8,386 rows | ✅ zero regressions |

**Verifier corrections of record (diagnoses, not verdicts):** (1) The C1a mojibake mechanism is NOT the body fallback chain — attachments never consult it; `_decode_text` in `attachment_extractor.py` hardcodes `utf-8/replace` (the documented DQ-H02 gap, now breaching its threshold). The 5 legacy files strict-decode **losslessly under windows-1251** (already in the body chain), not cp866 — so the fix is chain reuse, zero new codecs, recovering 564K of the 568K chars (99.3%). (2) The 2 `text/rtf` rows are a dispatch leak, not a deferral: the module's own invariants and tests route RTF to `unsupported_format`, but only the `application/rtf` spelling is pinned — `text/rtf` escapes through the `text/*` prefix path. (3) C7 is worse than claimed: `detail` is not only unpersisted, it is never logged — for live-synced corrupt attachments the exception class exists nowhere while the bytes are already gone.

#### Surviving findings — what blocks Phase B

| # | Finding | Severity | Phase B gate? |
|---|---|---|---|
| EQ-1 | `empty` misroutes content-bearing PDFs: 31/34 (91%) carry visible text (vector-glyph accounting print-outs — payroll, VAT, invoices — + sub-floor scans); excluded from the OCR backlog forever, bytes discarded on the live sync path = permanent loss of exactly the §2.2 high-value class | **HIGH** | **FIX BEFORE Phase B.** Every live sync run loses these documents irreversibly. Route vector-heavy / any-image zero-text PDFs to `scanned_pending_ocr` (vector-object or ink-coverage heuristic) so byte retention + Phase C OCR cover them |
| EQ-2 | Live credentials in `extracted_text`: `api_keys.txt` (108-char Anthropic key + OpenAI key) **+ verifier found a colleague's `.env`** (OpenAI + Gemini + Supabase), 1 AKIA hit, 7 password-pattern hits — all queryable, all headed for embeddings/Ask context | **HIGH** | **Rotate ALL keys immediately** (operational, today). Secret-pattern masking at ingest: must land **before Ask embeds the column**; Phase B itself may proceed. Resolves design open question 11 |
| EQ-3 | Attachment decode is utf-8-only (DQ-H02): 9 text-decode rows / 564K U+FFFD chars; files verified windows-1251 | MEDIUM | **FIX BEFORE/WITH Phase B** — one seam (`_decode_text` → body's strict chain), and the Phase B backfill is the natural re-extraction vehicle; recovers all 9 rows losslessly |
| EQ-4 | 44 text-decode rows store raw HTML/RTF source as `extracted` (no `_html_to_text` for HTML attachments; `text/rtf` prefix leak) | MEDIUM | **FIX WITH Phase B** (striprtf + HTML flattening are its scope) — but the backfill MUST key on `extractor_name='text-decode'` too: these rows' status says `extracted`, so an `unsupported_format`-keyed backfill silently skips them |
| EQ-5 | No cid-density guard in pdf.py (design §3.1 rule 3 unimplemented): 1 row 98.7% cid soup + 1 silent glyph-drop row, both status `extracted`, invisible to the OCR queue | MEDIUM | Ride along — required by **Phase C entry** (guard routes to `scanned_pending_ocr`); ~0.2% of extracted PDFs |
| EQ-6 | Broken-CMap garbage classified `extracted`: signed BG contract stored as Latin-lookalike mojibake (intrinsic to the PDF — no usable ToUnicode; both engines reproduce it); 1 confirmed row (~0.1%), Latin-script tail invisible to the census heuristic | MEDIUM | Ride along to **Phase C** — needs a script-mismatch / dictionary-hit heuristic (the design's replacement-char ratio would NOT catch printable lookalike garbage) |
| EQ-7 | `ExtractionResult.detail` dropped at both write seams and never logged: truncation reason, `image_only_pages=N` OCR backlog counts, and corrupt exception classes exist nowhere; recoverable only while the disk corpus exists | MEDIUM | **FIX WITH Phase B** — nullable `detail` column + wire the two `.values()`/constructor sites (existing never-embed-content invariant already written for it) |
| EQ-8 | 6 real PDFs (23.3 MB) hidden under `application/octet-stream` → `unsupported_format`; 1 is a scan (same bytes already in the OCR backlog via 3 `application/pdf` rows) | LOW | Ride along — this IS Phase B §2.10 magic-sniff; note the design's "~2 mislabeled PDFs" estimate is actually 6 |
| EQ-9 | Per-page header/footer repetition: 90/965 PDF rows (9.3%) carry a line ≥10× (worst ×499) — will dilute chunk embeddings in long manuals | LOW | Ride along — standard de-headering pass at **chunking time** (Ask), not an extractor change |
| EQ-10 | 8 case-insensitive alias groups / 7 persons (`'Yani Lozanov'`/`'Яни ЛОЗАНОВ'`) | LOW | **BY-DESIGN** (§3 B-14: byte-exact UNIQUE for idempotency, as-seen signal; zero movement since baseline). No action; normalize read-side |

**State-drift note:** after this pass, the 2026-06-12 Codex-review revision of `pdf.py` (dual-engine rescue before any scanned verdict) reset the 73 `scanned_pending_ocr` rows to `pending` as the backfill work queue. Verifier probes confirm the revised code re-classifies all probed docs back to `scanned_pending_ocr` — the next audit should expect `pending` = 0 after the backfill runs and the 73-row OCR backlog restored.

#### New baselines for the next audit

| Metric | Baseline (2026-06-11) |
|---|---|
| Text-bearing attachment rows / chars | 1,178 / 27,027,031 |
| Extraction-status census | extracted 1,145 (932 pdf + 213 text) / partial_scanned 28 / truncated 5 / scanned_pending_ocr 73 / empty 39 (37 pdf + 2 zero-byte json) / skipped_nondocument 6,119 / unsupported_format 2,683 |
| U+FFFD | 13 rows / 568,310 chars (post-EQ-3 target: ≤4 rows / ≤10 chars) |
| `(cid:)` rows / markup-source rows / husk rows | 21 / 44 / 0 (post-EQ-4/5 target: ≤20 / 0 / 0) |
| Repeated-line (≥10×) rows | 116 (9.8%) |
| Lone surrogates / C0 / CR | 0 / 0 / 0 (MUST stay 0) |
| Pipe-table PDF rows | 421/965 (43.6%) |
| `empty` PDFs misrouted | 31–34 of 34 (post-EQ-1 target: 0) |
| Hidden PDFs in unsupported_format | 6 (post-Phase-B sniff target: 0) |
| Body distribution (first explicit snapshot — closes the §7-regression gap) | n=8,386, 0 NULL, sum 24,703,378 chars, avg 2,946, p25/50/75/95 = 582/1,531/3,589/9,836, max 51,573, Σword_count 2,687,210 |
| Residual dup / body mojibake / same-MID groups / alias CI groups | 18 rows (0.21%) / 15 / 144 / 8 |

**§7.2 post-script — EQ-1 + the Codex scanned-before-rescue finding, FIXED same day (2026-06-12):** the extractor now (a) consults the pypdf rescue before any scanned verdict (one engine's blank read never discards a recoverable text layer — order-contract test pins it; empirically 0 of the 73 scanned rows were rescue-able, independently confirming them text-free on two engines), and (b) routes zero-text PDFs with vector/image content to `scanned_pending_ocr` instead of `empty` (`_has_undrawn_content`: any image, or ≥25 vector objects). The 37 lying `empty` rows were re-queued and re-judged: ALL 37 moved to the OCR queue — PDF `empty` is now an empty bucket (932 extracted / 110 scanned_pending_ocr / 28 partial / 5 truncated = 1,075, 100% accounted) and Phase C's backlog now honestly contains the payroll/VAT/invoice class. EQ-2 (live credentials in extracted_text) remains the user's rotation action + a Phase-B masking gate; EQ-3/4/7 fold into Phase B as triaged.
