# Connect — Email (IMAP) Ingestion & Entity Resolution — Design

> **Status:** agreed in design discussion (2026-06-06); not yet built. The IMAP *fetch* mechanism is
> validated as a standalone disk spike (`spikes/imap_fetch.py`, ran the full ~16 GB / 13,625-message
> mailbox, 0 errors). This doc is the design for graduating it into the app: **parse → DB → entities**.
> **Scope:** the Connect layer's email path — fetch, parse/decompose, store, resolve people/orgs.
> Cleaning/redaction, chunking/embedding (RAG), and summaries are downstream and out of scope here.
> **Anchors:** Bible §5.4 (connectors), §6 (4-layer memory), §15 (two-layer storage + entity
> resolution), §11.1 (HITL). Tenancy/RLS/erasure per `.claude/rules/security.md` + `docs/FIX_BEFORE_PROD.md`.

## 1. Storage model — process directly, NO raw archive (v1)

Flow is **fetch → parse → store structured rows in the DB**, then discard the raw RFC822 bytes.

- **Why:** the IMAP server is the system of record for originals; One AI is the *derived knowledge layer*.
  Keeping only the extracted text + metadata is GDPR **data-minimisation** (a DACH selling point) and
  avoids One AI becoming a multi-GB-per-mailbox copy of every inbox (smaller PII honeypot + erasure
  surface). A full mailbox measured at ~16 GB raw; the derived text is a fraction.
- **Resumability without a raw buffer:** the per-folder UID cursor + an idempotent upsert key
  `(org_id, connection_id, folder, uidvalidity, imap_uid)`. A crash re-runs from the cursor and
  **re-fetches the in-flight batch from IMAP**; `ON CONFLICT DO NOTHING` makes already-stored a no-op.
- **The trade (accepted):** re-*parsing* or re-*extracting attachments* later (better MIME handling,
  OCR) requires a **re-fetch**, and is **impossible once the email is deleted from the mail server**.
  The extracted *text* survives in the DB forever; the original *bytes* don't. All AI/Learn re-work
  (re-chunk, re-embed, re-summarise, re-resolve) runs off the kept text, so it is unaffected.
- **The seam (kept):** raw/attachment-byte storage stays behind a `RawBlobStore`-style interface,
  **unimplemented**. If reprocess-without-re-fetch ever becomes a real need — most likely **attachment
  original bytes** (extraction is lossy/improvable; users may want the file) — we add an **object store**
  (S3 / EU-sovereign **MinIO**) behind that interface without reworking the pipeline.

## 2. The pipeline — staged with durable hand-offs (no broker)

```
fetch (IMAP) → parse + resolve → structured rows in DB → [clean → chunk/embed → summarise]
```

- **Fetch + parse + deterministic entity-resolution run in ONE pass** — parse and email-key resolution
  are fast/cheap and overlap the network wait, so structured data is queryable as the sync progresses.
- **The genuinely-decoupled stage is the AI work (embedding, summaries)** — slow (model-bound). It reads
  the kept text from the DB and works the backlog asynchronously via a **DB work-queue** (claim with
  `SELECT … FOR UPDATE SKIP LOCKED`). No Redis/Kafka — in-process async + a DB queue is enough (Bible
  §15); more worker replicas pull the same queue to scale.
- **Per-message error isolation:** a parse failure on one email marks it `parse_failed` for retry and
  never aborts the run.

## 3. Production execution — what happens when a user clicks "Sync"

- `POST /connectors/{id}/sync` (company-admin, org-scoped) → atomic claim (single-runner) → **202**
  immediately (409 if a fresh run is active). The user watches a **status** (`sync_status`:
  running → counts → idle), never waits on the request.
- A **background job** runs the fetch using the point-1 encrypted creds from the DB, on its **own**
  `scoped_session(org_id)` (NOT the request-bound session), committing per batch (resumable).
- **Pilot:** in-process (Bible §15). **Scale:** a dedicated worker process pulling the same DB queue —
  same code, different host.
- **The dev spike's local disk is NOT how prod stores anything** — see §1 (no raw archive); the durable
  store is the DB (+ an object store later only if attachments need it).

## 4. Email decomposition — what we extract (deterministic, no LLM)

Three org-scoped Layer-1 tables:

- **`email_message`** — `id, org_id, connection_id` · threading (`message_id, in_reply_to, references[]`)
  · `from_name, from_address` · `subject, sent_at, received_at` · `body_text` (**text only — no
  `body_html`**) · derived flags (`direction` in/out — *derived from `from_address` vs the mailbox
  account, NOT the folder*; `is_automated`, `is_reply`, `has_attachments`, `word_count`, `language?`) ·
  `headers` (JSONB — the **full** header set; near-free and hedges the no-raw decision for metadata) ·
  `parse_status` + timestamps.
  **One row per LOGICAL email — dedup by `Message-ID`; folders are dropped.** UNIQUE
  `(org_id, connection_id, message_id)`; an email with no `Message-ID` falls back to a content-hash key.
  (The per-folder fetch cursor — `uidvalidity` / `last_uid` — lives in the sync-state, not on this row.)
- **`email_recipient`** — `(id, org_id, email_id →, kind [to|cc|bcc|reply_to], name, address)` + a
  resolved `person_id` (set by §6). A table (not JSONB) so "every email involving X" is a clean query.
- **`email_attachment`** — `(id, org_id, email_id →, filename, content_type, size_bytes, content_hash,
  is_inline, content_id, extracted_text)`. **Lean (decided): text is extracted inline at parse and the
  original bytes are discarded — no object store.** (Re-extraction with a better OCR would need a re-fetch;
  the `RawBlobStore` seam is kept so we can add attachment-byte storage later if that changes.)
- `received_at` (server internal date) is more trustworthy than `sent_at` (the forgeable Date header).

## 5. People & organizations — the entity spine

The connective tissue of the unified memory. **Email is the deterministic *match key*, not the person's
primary key** — a person owns *many* emails (work/personal/aliases). Tenant-scoped (each org has its own
graph; tenant A's "Boyan" ≠ tenant B's).

```
person          (id, org_id, display_name, is_internal, first_seen, last_seen)
person_email    (id, org_id, person_id, email,  UNIQUE(org_id, email),  source)   ← THE match key
person_alias    (id, org_id, person_id, alias, source)
company         (id, org_id, name, is_internal)        ← the EXTERNAL company entity (see naming note)
company_domain  (id, org_id, company_id, domain,  UNIQUE(org_id, domain))          ← org-by-domain, multi-domain-safe
person_company  (person_id, company_id)
```

- **Match by email → get-or-create the person** (the UNIQUE constraint + `ON CONFLICT` also resolves the
  parallel-worker create race). The person then *accumulates* emails, and later meetings/Slack/docs →
  the cross-source **dossier** (the "Ask" payoff).
- **Edge-case guards (bake in from day one):** role/shared mailboxes (`info@`, `noreply@`) are **not**
  people (use `is_automated`); a name-only fallback merge must be **guarded by a shared non-generic
  domain** (avoids same-name/different-company over-merge — a real bug class); **generic domains**
  (`gmail.com`) are not companies (skip-list); **multi-domain companies** unify via `company_domain`.
  Name-only sources (Slack/transcripts with no email) may **fragment** — an accepted limit of the
  deterministic approach.
- **NAMING (decided):** the external-company entity is **`company`** (NOT `organization`, which clashes
  with the tenant key `org_id`). `org_id` always means *the tenant*; `company` is an external org.

## 6. Entity resolution — deterministic backbone → the "pit" → HITL → (LLM later)

The ~90% clean path is deterministic; the ambiguous tail is resolved by a human first, an LLM later.

- **Deterministic backbone:** known address → link; brand-new address resembling nobody → create a
  person; both silent (no review).
- **The "pit":** an address that **looks like it might be an existing person** (e.g. `boyan@gmail` when
  `Boyan <boyan@company>` exists) is parked in a holding queue where **evidence accumulates** (more
  emails → signature, thread co-occurrence, domain).
- **Resolve in tiers, LLM last:** (a) cheap deterministic signals (signature → name/company; thread
  co-occurrence; display-name + domain); (b) an **LLM disambiguator** only for the still-ambiguous
  (bounded: pick among candidate persons + "new" — tractable, unlike open-ended synthesis); (c)
  low-confidence → **Human-in-the-Loop**.
- **v1 = human-only review queue (NO LLM):** a quiet "**N contacts to confirm**" indicator → a small
  **batched** list (not popups) → the **mailbox owner** (privacy: their own contacts, never an arbitrary
  admin) one-clicks *"Is this Boyan? Yes / No, new / Not a person."* Candidates proposed by simple
  name/domain similarity. Every human pick = **labelled data** that later trains/validates the LLM
  resolver. (UI: the `clari-pulse` "needs your approval" affordance — Bible §11.1.)
- **Progression:** v1 human confirms → v2 LLM *proposes* + human one-clicks → v3 LLM auto-links
  high-confidence, human sees only the unsure ones.
- **Guardrails (non-negotiable):** every person↔email link carries **provenance**
  (`matched_by: email_key | signal | llm | human`) + a confidence, and links are **reversible** (a wrong
  merge pollutes a whole dossier). Email-key links are gospel; LLM links are *proposals* until
  confirmed / above threshold. The LLM (when added) reads email content → **EU/local model +
  zero-retention**, and it is **Betriebsrat-sensitive** (AI-inferred employee attribution).
- **Measure the orphan rate first:** if the deterministic key handles 90 %+, the pit is a cheap tail; if
  it's large, improve the matcher (signature/domain handling) before adding an LLM.

## 7. Tenancy, security, erasure

Every table above is org-scoped: `org_id` NOT NULL + the inert `org_isolation` RLS policy + a cross-tenant
negative test, and joins the erasure obligation. The RLS engine-flip remains the hard gate before this
content reaches a real tenant (tracked in `docs/FIX_BEFORE_PROD.md`). Per-source/mailbox access control
(who may retrieve which mailbox's data) is the access-control-below-`org_id` work, tracked separately.

## 8. Storage architecture & connector lifecycle

### One DB, two layers, a shared Core
- **ONE PostgreSQL database** (Bible §6 "single PostgreSQL + pgvector"; §9 shared Core). NOT a separate
  per-connector DB — cross-source entity resolution *requires* shared `person`/`company` tables.
- **Layer 1 — source-specific tables** (per connector: `email_message` / `email_recipient` /
  `email_attachment`; later `slack_message`, `meeting`): the connector's natural parsed data; it owns them.
- **Shared Core** — `person` / `person_email` / `company`: connector-agnostic resolved entities every
  connector merges into. This is what makes it "**one** AI", not four silos.
- **Layer 2 — unified `chunks`** (text + embedding + keyword index): the clean, searchable representation
  *all* connectors feed.
- **"Cleaning later" is a STAGE, not a database:** `body_text` (Layer 1, raw extraction) → clean → chunk →
  embed → `chunks` (Layer 2), all in the same DB. The fully-cleaned body is transient (re-derivable from
  `body_text`; re-cleaning needs only `body_text`, NOT the discarded raw bytes — only re-*parsing* needs raw).
- **Provenance ("which connector"):** source rows via `connection_id` (→ connector + account); unified
  `chunks` via a `source_type` column; shared entities via provenance tags (e.g. `person_email.source`).
  Everything is traceable to its source — not a blanket connector column on every table.

### Connector lifecycle — pause / disconnect / delete (three distinct states)

| Action | Sync | AI access to existing data | Data kept? |
|---|---|---|---|
| **Pause** | stops | still used | yes |
| **Disconnect** | stops | **revoked — AI blind to it** | yes (reconnect restores, no re-sync) |
| **Delete** | stops | none | **purged** (erasure cascade) |

- **Disconnect = revoke the AI's access** — the privacy-first default and the expected behaviour. The
  connection's status flips to *disconnected* and ALL its data is excluded from retrieval; data is retained
  so a reconnect doesn't force a full re-sync.
- **Enforced at retrieval, NOT cosmetically:** every tool / search / SQL the AI runs is scoped to **active
  connections only**; disconnected sources are excluded. *(Lift v2's pattern — `semantic_search` connector
  filter + `run_sql_query` rejecting inactive-connector tables + dossier filtering — but make "disconnected"
  a **persistent connection state**, not v2's per-chat `chat_session.active_connectors` toggle.)*
- **Shared-entity visibility rule:** the AI can see a chunk / message / person **iff it is backed by ≥1
  ACTIVE source.** Disconnect Slack → Slack content/chunks/provenance go dark; a person known *also* via
  email stays (via email); a person known *only* via Slack becomes invisible.
- **Delete = a real DB purge** (the `CA-CONN-01` erasure cascade): removes the connection's source rows +
  chunks + its contributions to the shared graph, deleting a shared `person`/`company` only if no other
  source still references it. *(v2's "reset" only cleared its on-disk dump, never the DB — net-new here.)*
- **Connector *type* removal (code) is a separate concern:** `connectors/imap/` is a removable module
  (point-1's registry skips a missing connector without crashing the app); the data/tables persist until an
  explicit delete/migration. **Code-modular, data-durable** — distinct from pause/disconnect/delete.

## 9. Decisions log

**No open decisions for step 3** (parse → DB → entity resolution) — all settled.

**Open downstream** (decide when we reach them; not step-3 blockers): the **embedding model** (local/EU —
fixes the vector dimension, for the embed step) · **mailbox consent & scope + Betriebsrat posture** (before
selling) · the **RLS engine-flip timing** (the gate before real-tenant content).

**Decided:** external-org entity = **`company`** · **attachments — text extracted inline, original bytes
discarded** (no object store; `RawBlobStore` seam kept) · dedup by `Message-ID` + drop folders · `body_text`
only (no `body_html`) · derived flags computed at parse · **one DB / two layers / shared Core** ·
**disconnect revokes AI access but RETAINS the data** (reversible — reconnect restores, no re-sync), enforced
at retrieval · **hard-delete only on an explicit Delete** (the `CA-CONN-01` erasure purge).
