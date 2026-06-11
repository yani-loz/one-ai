# IMAP connector (Connect, step 3) — Dynamic Adversarial Validation

| | |
|---|---|
| **Date** | 2026-06-09 |
| **Target** | 10 · code `IM` (`testing/10_imap-connector/`) |
| **Scope** | The first **Connect**-layer feature end-to-end: connection management + credential cipher (`connectors/`), sync lifecycle (claim → fetch → cursor → resume), parse + data-quality (`imap/parsing/`), persistence (`email_message`/`email_recipient`/`email_attachment`), and the shared entity graph (`person`/`person_email`/`company`/`company_domain`). |
| **Method** | Five adversarial suites (A–E) executed **live** against the running docker stack — per-suite agents wrote the `TC-IM-*` case files; this doc consolidates. 42 cases (A–E inclusive; E01 executed earlier, A–E this pass = 41). |
| **Stack** | `docker compose` live: `db` (pg16/pgvector), `backend` (:8000). `app_env=local`. **RLS ENFORCED** as of migration `0009` (the tenant engine is `oneai_app` `NOBYPASSRLS`). |
| **Contract source** | No `CA-CONN` epic exists; acceptance criteria derive from `docs/connect-email-ingestion-design.md` + `CON-01_imap_email.md` + three deep code maps. Tags triaged against `docs/FIX_BEFORE_PROD.md` (the `CA-CONN-01..05` deferrals). |
| **Posture** | All writes used **run-stamped throwaway orgs** under `uuid4`-stamped scratch identities; every harness cleaned up its own rows (verified zero residue). Demo/globex orgs never touched. No writes under `backend/`/`frontend/`. |

---

## 1. Headline

**The hardest rule holds — RLS now bites live on the densest-PII email tables — but the never-lose-mail contract does NOT: two ❌ Fails (C01, C02) silently drop genuine mail, and a third silent-loss class (B08) reproduces under an injected transient fault.**

This is **not** an "all defenses held" pass. Tenant isolation is sound everywhere it was tested (no cross-tenant read/write/leak found in any suite); the failures are in **mail-completeness and data-quality**, not in the multi-tenancy boundary.

- **✅ Tenant isolation held end-to-end.** The cross-tenant non-negotiable (org B presenting org A's connection id to every verb → 404, no A-data, A's row survives B's delete) held on all five verbs (A04). RLS bites live as the real `NOBYPASSRLS` `oneai_app` role on all three email Layer-1 tables (E02) and on the entity graph (E01); the org GUC is strictly transaction-local with no cross-pool bleed (E07). **No cross-tenant exposure was found in any of the 42 cases.**
- **❌ Two genuine contract violations, both silent mail loss.** **C01** — a ~300-deep multipart raises `RecursionError` inside `parse_email`, violating its documented *never-raises* contract; through a real `ConnectorSyncRunner` the email is caught → `failed` → its UID is stepped over, while the run **finalizes `status=succeeded`** and the email's identity is permanently lost. **C02** — a decoy email planting a chosen `Message-ID` makes a later, genuinely-different email reusing that id be **silently SKIPPED** by the `exists()` short-circuit; the dedup key trusts an attacker-influenceable header with no content-hash cross-check. Both bounded to one org+connection → **Medium**.
- **🆕 B08 (Medium) is the same silent-loss class one step removed.** A non-dedup `IntegrityError` on one UID (standing in for a transient deadlock/blip) routes to `tracker.fail`: the UID lands in `failed_uids` **and the cursor advances past it to the next UID** — the runner makes no transient-vs-permanent distinction and has no retry budget, so one transient fault permanently and silently drops the email. It is a ⚠️ (not ❌) only because it needs an *injected* fault, where C01/C02 do the wrong thing on input they should handle.
- **🆕 E08 (Medium, latent flag) — a fail-open surface, not an active leak.** Production is correctly enforced (the `SyncRunner` uses `scoped_session(org_id)` → `oneai_app` with the GUC bound, RLS bites). But `EmailIngestService` is engine-agnostic and the dev/test conftest + dump driver build it on the `BYPASSRLS` global engine — live-confirmed to write+read with no RLS. No active leak today (prod-path-first), but a latent architectural fail-open if a future prod path mis-wires ingest to the global engine.

### NEW findings by severity — 19 total (4 Medium · 13 Low · 2 Info)

| Severity | Findings |
|---|---|
| **Medium (4)** | B08 (transient fault → permanent UID drop) · C01 (deep-multipart `RecursionError` → silent drop @ `succeeded`) · C02 (dedup-poison silently skips a genuine email) · E08 (ingest engine-agnostic → BYPASSRLS fail-open surface) |
| **Low (13)** | A08 (no decrypt-by-version → key rotation bricks decrypt) · B02 (disable doesn't stop an in-flight run) · B03 (TOCTOU returns the wrong 409 reason) · B04 (STALE_SECONDS reclaim of a still-live run — wasted work, **no double-count**) · B09 (`failed_uids` unbounded) · B10 (corrupt fencing token wedges ≤5 min, fail-safe) · C03 (direction spoof, no SPF/DKIM) · C04 (C0 control-char survival) · C05 (no size limit) · C06 (unbounded `references` array) · C08 (forgeable `received_at` on the no-INTERNALDATE fallback) · E04 (generic-subdomain company over-merge) · E06 (`normalize_email` internal-whitespace gap, not reachable via the parser) |
| **Info (2)** | A02 (no AAD → credential blob portable across rows/orgs) · D04 (binary mislabeled `text/plain` → replacement-char soup) |

**2 ❌ Fail (C01, C02). 0 REFUTES-FIXED.** The two CONFIRMS-FIXED regression cases (C09, C10) and the four email/entity RLS confirmations all held.

---

## 2. Results (42 cases)

Legend: ✅ defense held · ⚠️ pass-with-concern · ❌ fail · 🆕 NEW · ✔ CONFIRMS-FIXED · 📋 CONFIRMS-DOCUMENTED

> **Label note (B-suite normalization):** the Suite-B agent recorded B03/B08/B09/B10 as "Pass" despite each carrying a NEW concern. To keep the table internally coherent with the rest of the pass (✅ = clean hold; ⚠️ = a NEW finding rides along), they render here as ⚠️. This is a legend normalization, not a re-judgment — every finding's text and severity is verbatim from the suite agents. C01/C02 stay ❌ (wrong behavior on input they must handle); B08 stays ⚠️ (needs an injected transient fault).

### Suite A — Connection plane & credential cipher (8)

| ID | Case | Result | Tag | Sev |
|---|---|---|---|---|
| TC-IM-A01 | Credential cipher fails closed (wrong key / tampered tag / truncated blob) | ✅ | — | — |
| **TC-IM-A02** | **NEW — no AAD → `secret_ciphertext` blob portable across rows/orgs** | ⚠️ | 🆕 | Info |
| TC-IM-A03 | Schema rejects smuggled `org_id` / invalid `connector_type` (422) | ✅ | — | — |
| TC-IM-A04 | Cross-tenant access on every verb → 404, no A-data leak (the non-negotiable) | ✅ | — | — |
| TC-IM-A05 | Response carries no secret; weak key → 503, key not echoed | ✅ | — | — |
| TC-IM-A06 | Role + auth gates: member 403, missing token 401 | ✅ | — | — |
| TC-IM-A07 | Concurrent duplicate create → exactly one 201, losers 409, no 500 | ✅ | — | — |
| **TC-IM-A08** | **NEW — key rotation silently breaks decrypt (no decrypt-by-version)** | ⚠️ | 🆕 | Low |

### Suite B — Sync lifecycle, claim & resumability (10)

| ID | Case | Result | Tag | Sev |
|---|---|---|---|---|
| TC-IM-B01 | Two concurrent POST /sync → one 202 + one 409, one ledger row | ✅ | — | Info |
| **TC-IM-B02** | **NEW — disable mid-run does not stop an in-flight sync** | ⚠️ | 🆕 | Low |
| **TC-IM-B03** | **NEW — TOCTOU disable returns the wrong 409 reason** | ⚠️ | 🆕 | Low |
| TC-IM-B04 | Stale-claim reclaim does **NOT** double-count mail (hypothesis refuted) | ⚠️ | 🆕 | Low |
| TC-IM-B05 | Delete during active run aborts cleanly via CASCADE (no orphans) | ✅ | — | Info |
| TC-IM-B06 | UIDVALIDITY reset resets the cursor floor (never-lose-mail held) | ✅ | — | Info |
| TC-IM-B07 | Requested-but-unreturned UID stops the cursor (no skip) | ✅ | — | Info |
| **TC-IM-B08** | **NEW — misclassified transient error → UID stepped over forever (mail loss)** | ⚠️ | 🆕 | **Med** |
| **TC-IM-B09** | **NEW — `failed_uids` array grows unbounded across runs** | ⚠️ | 🆕 | Low |
| **TC-IM-B10** | **NEW — corrupt `sync_run_id`→NULL wedges the connection (fail-safe, ≤5 min)** | ⚠️ | 🆕 | Low |

### Suite C — Parse & data quality (10)

| ID | Case | Result | Tag | Sev |
|---|---|---|---|---|
| **TC-IM-C01** | **FAIL — deep-nested multipart → `RecursionError` → silent permanent drop @ `succeeded`** | ❌ | 🆕 | **Med** |
| **TC-IM-C02** | **FAIL — dedup poisoning: reused Message-ID silently skips a genuine email** | ❌ | 🆕 | **Med** |
| **TC-IM-C03** | **NEW — direction spoof: `From` == mailbox → outbound (no SPF/DKIM)** | ⚠️ | 🆕 | Low |
| **TC-IM-C04** | **NEW — C0 control-char survival (all but NUL) into subject/body/headers** | ⚠️ | 🆕 | Low |
| **TC-IM-C05** | **NEW — no size limit → oversized email/attachment fully materialized + hashed** | ⚠️ | 🆕 | Low |
| **TC-IM-C06** | **NEW — unbounded `references` array** (header-bloat half is 📋 CA-CONN-05) | ⚠️ | 🆕 | Low |
| TC-IM-C07 | Charset lie / unknown charset / RFC 2047 bomb → never raises | ✅ | — | — |
| **TC-IM-C08** | **NEW — forged/missing Date, no INTERNALDATE → attacker-controlled `received_at`** | ⚠️ | 🆕 | Low |
| TC-IM-C09 | Regression: over-255 Content-Type + NUL in text attachment still ingests | ✅ | ✔ | — |
| TC-IM-C10 | NUL in subject/body/Message-ID → stripped, no insert crash | ✅ | ✔ | — |

### Suite D — Attachments (5, binary extraction deferred — CA-CONN-04)

| ID | Case | Result | Tag | Sev |
|---|---|---|---|---|
| TC-IM-D01 | Zip-bomb / decompression-bomb attachment is **INERT** today | ✅ | 📋 | High† |
| TC-IM-D02 | Billion-laughs / XXE in an `application/xml` attachment is **INERT** | ✅ | 📋 | High† |
| TC-IM-D03 | PDF/Office attachment with a macro/exploit is **INERT** | ✅ | 📋 | High† |
| **TC-IM-D04** | **NEW — binary blob mislabeled `text/plain` → replacement-char soup** | ⚠️ | 🆕 | Info |
| TC-IM-D05 | Filename `../../etc/passwd` stored verbatim, never used as a path (**LATENT**) | ✅ | 📋 | High† |

† The High on D01/D02/D03/D05 is the **future gating risk** when CA-CONN-04's binary extractor lands, **not an active defect** — all four are inert/latent today (see §4).

### Suite E — Persistence, RLS & entity graph (9)

| ID | Case | Result | Tag | Sev |
|---|---|---|---|---|
| TC-IM-E01 | Live RLS on `person`/`person_email` as `oneai_app` (executed earlier) | ✅ | ✔ | — |
| TC-IM-E02 | Live RLS on `email_message`/`email_recipient`/`email_attachment` as `oneai_app` | ✅ | ✔ | — |
| TC-IM-E03 | Same display name, different addresses → two distinct persons (no over-merge) | ✅ | — | — |
| **TC-IM-E04** | **NEW — generic free-mail *subdomain* slips the skip-list → company over-merge** | ⚠️ | 🆕 | Low |
| TC-IM-E05 | Role-address guard + conservative-list boundary (correct-by-design) | ⚠️ | — | Info |
| **TC-IM-E06** | **NEW — `normalize_email` internal-whitespace gap (not reachable via the parser)** | ⚠️ | 🆕 | Low |
| TC-IM-E07 | Org GUC must not leak across pooled transactions (transaction-local) | ✅ | — | — |
| **TC-IM-E08** | **NEW — ingest path runs on the BYPASSRLS global engine (latent fail-open)** | ⚠️ | 🆕 | **Med** |
| TC-IM-E09 | Concurrent get-or-create resolves via SAVEPOINT re-read (no dup, no abort) | ✅ | — | — |

---

## 3. The ❌ Fails (the never-lose-mail breaches)

### TC-IM-C01 — deep-multipart `RecursionError` → silent permanent drop (Fail, Medium, NEW)

Live: a 300-deep multipart raises `RecursionError` in bare `parse_email`, **violating the never-raises contract**. Through a real `ConnectorSyncRunner`, `_ingest_one` catches it → `failed` → `tracker.fail(uid)` → the cursor advances past the UID and records it in `failed_uids`. **The teeth:** the run finalizes `status=succeeded` while the crafted email's identity is permanently lost — only a failed-count remains (no UID/reason surfaced to the operator), and it is never retried until a UIDVALIDITY reset.

**Why Medium, not High:** bounded to one org+connection, requires a crafted message, no cross-tenant exposure. But it is a *contract violation* (the parser's documented promise is "never raises") and a *silent* loss reported as success — that pairing is the worst-case shape for a never-lose-mail system.

**Remediation:** cap multipart recursion depth in `parse_email` (return a partial/flagged parse rather than raising); separate a *parse-failure* outcome from a *clean success* so the run does not finalize `succeeded` with un-surfaced drops; and do not advance the cursor past a never-parsed UID (or keep it in a durable, surfaced dead-letter rather than a count).

### TC-IM-C02 — dedup poisoning silently skips a genuine email (Fail, Medium, NEW)

Live ingest: a decoy email planting a chosen `Message-ID` causes a later, genuinely-different email (payload `WIRE THE 2M NOW`) reusing that id to be **silently SKIPPED** by the `exists()` short-circuit — only the decoy row survives. `dedup_key` trusts an attacker-influenceable header with **no content-hash cross-check**, so anyone who can get one email into the mailbox can pre-register a `Message-ID` and suppress a future genuine message at that id.

**Why Medium:** bounded to one org+connection (the dedup key is org+connection scoped — no cross-tenant reach), and it requires the attacker to plant the decoy first. The integrity impact (a real business email made to vanish with no error) is what keeps it at Medium rather than Low.

**Remediation:** include a content hash (or `(internaldate, from, subject, size)` tuple) in the dedup decision so a colliding `Message-ID` with different content is stored, not skipped — or treat a Message-ID collision with mismatched content as a new row rather than a dup.

---

## 4. The NEW findings (detail + honest severity calibration)

### Medium

**TC-IM-B08 — misclassified transient error steps the cursor over a UID forever (`connector_sync_runner.py:295-312`).** A non-dedup `IntegrityError` on one UID (injected via a monkeypatched `EmailIngestService`, standing in for a transient deadlock/blip) routed to `tracker.fail`: UID 2 landed in `failed_uids` **AND** the cursor advanced past it (`last_seen_uid=3`). The runner makes no transient-vs-permanent distinction and has no retry budget, so one transient fault permanently and silently drops the email. **⚠️, not ❌**, because it needs an injected fault — but it is the same silent-loss class as C01/C02 and pairs with B09 (the `failed_uids` it writes into is itself unbounded). *Remediation:* classify dedup-`IntegrityError` (skip-safe) vs. everything else (retry-safe); give failed UIDs a bounded retry budget before they are abandoned, and never advance the cursor past a UID that was never durably stored.

**TC-IM-E08 — ingest is engine-agnostic; dev/test wires it to the BYPASSRLS global engine (flag).** **Production is enforced today** — `connector_sync_runner` uses `scoped_session(org_id)` (live-confirmed `oneai_app` tenant role, GUC bound, RLS bites). But `EmailIngestService` takes whatever session it's handed, and the dev/test conftest + dump driver build it on `GlobalSessionLocal` (`BYPASSRLS`, GUC unset) — live-confirmed to write+read with no RLS. Not an active leak (the prod path is correctly scoped), but a **latent architectural fail-open surface** if a future prod path mis-wires ingest to the global engine. *Remediation:* make `EmailIngestService` require a tenant-scoped session (or assert the GUC is bound) so a global-engine wiring fails loudly rather than silently bypassing RLS.

### Low

- **TC-IM-A08 — no decrypt-by-version (`credential_cipher.py` / `SECRET_KEY_VERSION` constant).** `secret_key_version` is stored on every row but decrypt always uses the current process key; there is no keyring. Rotating `CONNECTOR_SECRET_KEY` **bricks every existing connection's credential** with no in-product recovery (`/test` → HTTP 200 `status='error'`, no 500). Fail-shut, no leak. **Not tracked in `FIX_BEFORE_PROD`** (which covers JWT/DB-password rotation, not connector decrypt-by-version). *Remediation:* a versioned keyring keyed on the stored `secret_key_version`, so a rotation re-wraps rather than bricks.
- **TC-IM-B02 — disable does not stop an in-flight run (`connector_sync_runner.py:158-233`).** `_sync` never re-checks `disabled_at`; a disable landing after the claim still ingests, advances `last_synced_at`, and finalizes `idle` against a disabled connection. "Disable" is a *future-claim gate*, not an in-flight kill switch — a design gap, not corruption. *Remediation:* re-check `disabled_at` at each batch boundary and abort cleanly if set.
- **TC-IM-B03 — TOCTOU disable returns the wrong 409 reason (`sync_service.py:75` vs `:79`).** A `disabled_at` landing between the disabled-check and `claim_for_sync` makes the claim's `disabled_at IS NULL` predicate fail → `SyncAlreadyRunningError` instead of `ConnectorDisabledError`. The caller is told "already running" when the truth is "disabled" — a misleading error in a narrow race; no leak/loss. *Remediation:* on a claim miss, re-read state and raise the accurate error.
- **TC-IM-B04 — stale-reclaim does NOT double-count (hypothesis refuted; concern retained).** The predicted double-count did **not** reproduce: after aging the heartbeat past `STALE_SECONDS` and reclaiming with a re-streaming run, the `email_message` count stayed at 2 (the fence + `uq_email_message_dedup` make re-ingest idempotent). Residual concern only: a `STALE_SECONDS` set below real batch wall-time would reclaim a still-live run (wasted work, two-runner window) — no data-integrity defect. Logged NEW for the wasted-work window; the headline break-hypothesis is a Pass.
- **TC-IM-B09 — `failed_uids` grows unbounded.** Across 50 simulated runs it grew to length 50 with no cap, re-serialized on every batch commit; it clears only on a UIDVALIDITY reset. A poison-heavy mailbox bloats the cursor row indefinitely. Small practical blast radius but genuinely unbounded; pairs with B08. *Remediation:* cap/ring-buffer `failed_uids`, or move abandoned UIDs to a separate dead-letter table.
- **TC-IM-B10 — corrupt `sync_run_id`→NULL wedges the connection (fail-safe, ≤5 min).** With `sync_run_id` NULL while `sync_status='running'`, the live run's fenced heartbeat returns False (all writes miss) and a fresh claim is blocked while the heartbeat stays fresh — wedged in a `running`+NULL state owned by nobody. Aging past `STALE_SECONDS` lets a reclaim self-heal it. Fail-safe (no leak/loss), bounded ≤5 min, requires an out-of-band corrupting write.
- **TC-IM-C03 — direction spoof, no SPF/DKIM.** `derive_direction` is a bare string compare (`From` == mailbox → `outbound`); any "emails I sent" view is poisonable by a trivially spoofed `From`. No authentication-results check anywhere in the parse path. *Remediation:* a v2 direction signal that consults `Authentication-Results`/`Received` rather than a raw `From` compare.
- **TC-IM-C04 — C0 control-char survival (`headers.py:123-125` strips only NUL).** `\x01`, `\x07`, and `\x1b` (ANSI escape) survive parsing and persist verbatim into the stored subject and `body_text` — a log/terminal-injection and dirty-downstream-text surface for any unsanitised consumer. *Remediation:* strip/escape C0 controls (except `\t\n\r`) at the sanitize seam, paired with output-encoding at every consumer.
- **TC-IM-C05 — no size limit anywhere.** A 10 MB body + 8 MB `text/csv` attachment parse and sha256 fully with zero cap (`parse_email`/`_extract_body_text`/`_attachment_bytes`). A missing guardrail before the production fetch path streams unbounded messages. Demonstrated "no cap"; not pushed toward OOM on the shared container. *Remediation:* a configurable per-message/per-attachment size cap with a flagged-truncation outcome.
- **TC-IM-C06 — unbounded `references` array (the genuinely-untracked half).** A 1 MB `X-` header is stored verbatim — **this half is the documented CA-CONN-05 data-minimization deferral (📋 CONFIRMS-DOCUMENTED)**. The `references` array, however, is unbounded at 10,000 ids and is **not** tracked anywhere — an attacker can pad `References` arbitrarily for row bloat. Tagged 🆕 for the untracked half per single-tag guidance. *Remediation:* cap the `references` array length; fold into CA-CONN-05's allowlist decision.
- **TC-IM-C08 — forgeable `received_at` on the no-INTERNALDATE fallback (`email_repository.py:60`).** With no INTERNALDATE and no `Received` header, `received_at` is taken from the forgeable `Date` header — a 2099 date pins the email to the top of `list_for_org` (`received_at DESC NULLS LAST`); an absent date NULLs out and sorts last. **The production fetch supplies a real INTERNALDATE which neutralizes this**; the concern is the disk/no-INTERNALDATE fallback path. *Remediation:* clamp a future `received_at` to ingest time, or prefer a server/ingest timestamp over the `Date` header.
- **TC-IM-E04 — generic free-mail *subdomain* over-merge.** `gmail.com` correctly mints a person but no company. But `_GENERIC_DOMAINS` is an **exact-match set**, so `mail.gmail.com` (any free-mail subdomain) slips the skip-list and mints a Company, falsely linking unrelated personal-subdomain senders as colleagues — the one genuine over-merge exception to the resolver docstring's "never over-merge" claim. Org-scoped, recoverable, no cross-tenant exposure. *Remediation:* suffix-match (or registrable-domain match) the generic-domain skip-list.
- **TC-IM-E06 — `normalize_email` internal-whitespace gap (not reachable via the parser).** The break hypothesis does **not** reproduce through the connector — `getaddresses` collapses the space adjacent to `@` (`info @x.com` → `info@x.com`), so the role guard holds and no person is minted. The latent finding is at the resolver seam: `normalize_email` does not strip internal whitespace, so a non-`getaddresses` key would dodge `is_role_address` and mint a malformed-key person. Data-quality only, not a role-guard bypass, **not reachable via the current IMAP parser**. *Remediation:* strip internal whitespace in `normalize_email` so the resolver is robust to non-parser callers.

### Info

- **TC-IM-A02 — no AAD → credential blob portable across rows/orgs (`credential_cipher.py`).** `encrypt`/`decrypt` pass `associated_data=None`, so nothing binds a credential blob to its org/row. Proven at cipher level (same bytes decrypt under a second instance) **and** at DB level (org A's ciphertext transplanted onto org B's row decrypts to org A's secret). **Not exploitable today** — it needs a direct `secret_ciphertext` write, gated by RLS + `get_in_org` — a defense-in-depth gap. *Remediation:* bind `org_id`/`connection_id` as AES-GCM AAD so a transplanted blob fails authentication.
- **TC-IM-D04 — binary mislabeled `text/plain` → replacement-char soup.** A PNG+binary blob labeled `text/plain` is decoded (`errors='replace'`) to 513 `U+FFFD` chars with no crash — the never-raises contract holds, but the corrupt soup would be stored in `extracted_text` because the Content-Type is trusted with no content sniffing. Data-quality only (no crash/injection/cross-tenant exposure). *Remediation:* content-sniff (magic bytes) before trusting `text/*`, or drop undecodable blobs to `NULL` rather than storing soup.

---

## 5. RLS now enforced — the hardest rule holds (E01 + E02 + E07)

This pass is the first to prove **live row-filtering** on the Connect-layer tables as the real least-privilege role — RLS is no longer "defined but inert" for these tables.

- **TC-IM-E01 (prior, ✔):** RLS holds live on `person`/`person_email` as `oneai_app` — 7/7 checks, teeth fire (the global `BYPASSRLS` role sees both orgs), cross-org INSERT/UPDATE rejected with *new row violates row-level security policy*.
- **TC-IM-E02 (✔):** the same 7/7 on the **densest-PII** email tables — `email_message`/`email_recipient`/`email_attachment`. A-scoped app sees only A; the global role sees both (the anti-vacuity teeth); cross-org INSERT **and** UPDATE both rejected. Confirms the migration `0009` flip reached the email Layer-1 tables.
- **TC-IM-E07 (✅):** the org GUC is **strictly transaction-local** — sequential A→B reuse on one pooled connection carries no GUC over; interleaved concurrent two-org transactions each see only their own rows; the post-txn unscoped read fails closed (errors on `''::uuid` rather than leaking prior-org rows).

**The one caveat (E08, Med):** this enforcement is on the **production** path (`SyncRunner` → `scoped_session`). The latent fail-open is that `EmailIngestService` is engine-agnostic and the dev/test harness wires it to the `BYPASSRLS` global engine — so "RLS holds" is true for prod-as-wired-today, with a standing requirement that ingest never be handed a global session in a prod path. State both, or the highlight over-reads.

---

## 6. CONFIRMS-DOCUMENTED & CONFIRMS-FIXED (the deferrals/regressions proven once)

**CA-CONN-04 — binary attachment extraction deferred → all four binary attacks are INERT/LATENT today (D01/D02/D03/D05).** Proven not with vacuous `is-None` asserts but with checks that would actually fail if the dangerous behavior were present:
- **D01** zip-bomb — a 1 MiB-of-zeros blob (~1 KB compressed) returns `None` as `application/zip` and decodes to 17 chars when mislabeled `text/plain`; the extractor imports no `zlib`/`gzip`/`zipfile`/`tarfile`.
- **D02** billion-laughs/XXE — `application/xml` **is** decoded, but by a raw `bytes.decode` with no XML parser: `&lol9;` entities survive verbatim (`out_len == in_len`) and the `file:///etc/passwd` XXE yields no `root:`.
- **D03** PDF/Office macro — all five binary doc types (PDF `/OpenAction`, `.docx vbaProject`, ms-excel, msword, ms-tnef) return `None`, bytes never opened; a benign `text/plain` control extracts (proving `None` is a dispatch decision, not breakage).
- **D05** filename `../../etc/passwd` — retained verbatim (sanitize only NUL-strips + length-caps to 998), but a grep of `backend/app/connectors` shows zero filesystem ops; the filename only becomes a Postgres text column. **LATENT** — becomes live arbitrary-write/traversal if a `RawBlobStore`/binary extractor ever keys on the filename.

These are the **gating risks to harden when CA-CONN-04 lands** (the High severity is that future gate, not an active defect): a real archive extractor must cap decompression; a real XML parser must disable DTD/external entities; a binary store must not key on the attacker-supplied filename.

**CA-CONN-05 — verbatim full-header retention (C06, first half).** The 1 MB `X-` header stored verbatim is the documented data-minimization deferral (`docs/connect-email-ingestion-design.md` §4); the *unbounded `references` array* half of the same case is **not** tracked → tagged 🆕 (see §4).

**CONFIRMS-FIXED regressions (C09, C10):** the `7c90f55` fix holds — an over-255 Content-Type (capped to ≤255) and a NUL inside a `text/csv` attachment both ingest with 2 attachments, no silent drop (C09); and a NUL-bearing subject/body/Message-ID ingests with no crash and no NUL in any column (C10). **Mechanism correction (honest):** NUL is stripped by `raw_header → safe_str → strip_nul` *before* the dedup NUL-check, so a NUL Message-ID becomes the clean key directly and does **not** force the hash fallback — more robust than the hypothesis assumed; verdict stays CONFIRMS-FIXED, only the mechanism is corrected.

---

## 7. Limitations / scope

- **No live IMAP server.** No real IMAP server exists in the stack; every `runner`-mode case (Suite B in full, plus the runner half of C01) used the `FakeIncrementalConnector` + `make_registry` harness. The claim/cursor/fence/lifecycle logic is exercised end-to-end against real DB state, but the **network IMAP client** (`imap/client.py` FETCH/SEARCH, TLS, server quirks) was **not** exercised live — that is a separate pass.
- **C01's `RecursionError` was proven via bare `parse_email` + a real `ConnectorSyncRunner`**, not an end-to-end live FETCH of a 300-deep message off a real server. The runner consequence (caught → `failed` → cursor steps over → `succeeded`) is real; the network delivery of such a message is assumed.
- **`received_at`-forgery (C08) is the no-INTERNALDATE/disk fallback only** — the production fetch supplies a server INTERNALDATE that neutralizes it. The forgery was shown on the fallback parse path, not through a live server that omits INTERNALDATE.
- **CA-CONN-01 / 02 / 03 were NOT exercised this pass.** This pass proves CA-CONN-04 (binary attachments, inert/latent) and CA-CONN-05 (verbatim headers, C06 half). It does **not** exercise the **erasure wiring** for `connector_connection` (CA-CONN-01) or the email/entity tables (CA-CONN-03), nor the **SSRF egress validation** on `POST /connectors/{id}/test` (CA-CONN-02) — those remain as `FIX_BEFORE_PROD` deferrals untested here.
- **E08's enforcement claim is "prod-as-wired-today."** RLS bites on the `SyncRunner`-scoped path; the dev/test ingest-on-`BYPASS`-engine wiring is the live-confirmed fail-open surface, flagged Med rather than treated as an active leak.
- **No OOM was forced (C05).** Unbounded materialization was demonstrated as "no cap," not driven to container exhaustion (the stack is shared).

---

## 8. Recommended follow-ups (candidates for `FIX_BEFORE_PROD.md`)

1. **Fix the two silent-mail-loss Fails (C01, C02)** — multipart recursion cap + parse-failure-≠-success outcome; content-hash cross-check in the dedup key. **These are the priority** (a never-lose-mail system that silently drops mail is the core-contract break).
2. **Make the runner transient-aware (B08 + B09 + B10)** — classify dedup-`IntegrityError` vs. retryable faults, give failed UIDs a bounded retry budget, never advance the cursor past an un-stored UID, and cap/dead-letter `failed_uids`.
3. **Require a tenant-scoped session in `EmailIngestService` (E08)** — so a global-engine wiring fails loudly rather than silently bypassing RLS.
4. **Add connector key versioning (A08)** and **bind credential AAD (A02)** — a keyring keyed on `secret_key_version`, and `org_id`/`connection_id` as AES-GCM AAD.
5. **Data-quality guardrails (C03/C04/C05/C06/C08, E04/E06, D04)** — size caps, C0-control sanitisation, `references`-array cap, suffix-match the generic-domain list, content-sniff before trusting `text/*`, and clamp a future `received_at`.
6. **Track A08 (decrypt-by-version) in `FIX_BEFORE_PROD`** — it is currently untracked there.
