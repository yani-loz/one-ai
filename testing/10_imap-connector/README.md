# Target 10 — IMAP Connector (Connect, step 3)

> **Scope.** The first **Connect**-layer feature: the IMAP email connector end-to-end —
> connection management + credential encryption (`connectors/`), sync (claim → fetch → cursor →
> resume, `connectors/sync/` + `imap/sync/`), parse + data-quality (`imap/parsing/`), persistence
> (`email_message`/`email_recipient`/`email_attachment`), and the shared entity graph
> (`person`/`person_email`/`company`/`company_domain`). Adversarial, live, tenant-isolation-first.

## Provenance of the contract (read this)
There is **no `CA-CONN` epic under `docs/PM/`** (only platform-console + company-admin exist). The
acceptance criteria here are derived from **`docs/connect-email-ingestion-design.md`** (the agreed
design record) + **`CON-01_imap_email.md`** (the implementation spec) + the three deep code maps
produced for this pass. Tags are triaged against **`docs/FIX_BEFORE_PROD.md`** (esp. the `CA-CONN-01..05`
deferrals) so a tracked deferral is `📋 CONFIRMS-DOCUMENTED`, never `🆕 NEW`.

## Environment (corrects the root `testing/README.md`)
| Item | Value |
|---|---|
| Stack | `docker compose up` — db (pg16/pgvector), backend (:8000) |
| **RLS** | **ENFORCED as of migration `0009` — NOT inert.** The root `testing/README.md` env row ("defined but inert, app connects as superuser `oneai`") is **stale**. The tenant engine connects as **`oneai_app`** (`NOSUPERUSER`, **`NOBYPASSRLS`**); `oneai_global` (`BYPASSRLS`) serves only login/refresh/audit/cross-org flows; `oneai` (super) is DDL/seed only. `scoped_session(org_id)` binds `app.current_org_id` per transaction. |
| Roles seen live | `oneai super=t bypassrls=t` · `oneai_app super=f bypassrls=f` · `oneai_global super=f bypassrls=t` |
| Harness | Python over stdin into the backend container: `docker compose exec -T backend python - < testing/10_imap-connector/harness/<x>.py` |
| Safety | All writes use **run-stamped throwaway orgs**; harnesses clean up their own rows; the demo org is never touched. |

## Execution modes (not all cases are live-HTTP)
- **`pure`** — `parse_email` / `extract_text` are pure functions; run in-container, no DB.
- **`db-rls`** — asyncpg as `oneai_app` / `oneai_global` / owner, to prove DB-level RLS.
- **`ingest`** — `EmailIngestService.ingest_email(raw)` on a seeded connection (entity graph + dedup).
- **`runner`** — the `FakeIncrementalConnector` + `make_registry` harness (no live IMAP server exists) for sync/claim/cursor/lifecycle/concurrency.
- **`http`** — httpx against `:8000` with forged `company_token`s for route authz / cross-tenant 404.

## Case catalog (the test plan)
> `🆕`=NEW · `📋`=CONFIRMS-DOCUMENTED · `✔`=CONFIRMS-FIXED · `—`=positive/contract. Result is the **expected** verdict until executed.

### Suite A — Connection plane & credential security (`http` / `pure`)
| ID | Break hypothesis | Type | Mode | Exp. |
|---|---|---|---|---|
| TC-IM-A01 | Credential cipher fail-closed: wrong key / tampered tag / truncated blob → `ConnectorSecretError`, never garbage | Negative | pure | ✅ |
| TC-IM-A02 | Ciphertext has **no AAD** → a blob is portable across rows/orgs (decrypts under the shared key regardless of connection) | Adversarial | pure | 🆕 Info |
| TC-IM-A03 | `org_id` smuggled in `POST /connectors` body → 422 (`extra=forbid`), never silently honored | Negative | http | ✅ |
| TC-IM-A04 | Cross-tenant `GET/test/disable/enable/delete /connectors/{A}` by org B → 404, no A-data, no `last_error`/username leak | Adversarial | http | ✅ |
| TC-IM-A05 | `ConnectionResponse` never exposes password / ciphertext / key-version; weak key → 503, key not echoed | Negative | http | ✅ |
| TC-IM-A06 | member token / missing token on every connector route → 403 / 401 | Negative | http | ✅ |
| TC-IM-A07 | Duplicate `(org,type,username)` create race → exactly one 201, the loser 409 not 500 | Concurrency | http | ✅ |
| TC-IM-A08 | `secret_key_version` stored but no decrypt-by-version logic → a rotated `connector_secret_key` silently breaks every existing connection's sync | Adversarial | pure | 🆕 Low |

### Suite B — Sync lifecycle, claim & resumability (`runner`)
| ID | Break hypothesis | Type | Mode | Exp. |
|---|---|---|---|---|
| TC-IM-B01 | Two concurrent `POST /{id}/sync` → exactly one 202 + one spawn, one 409, one `running` ledger row (the conditional UPDATE is the only gate) | Concurrency | http | ✅ |
| TC-IM-B02 | **Disable mid-run does NOT stop the in-flight sync** — `disable` only sets `disabled_at` (gates future claims); the running batch keeps ingesting into a "disabled" connector | Adversarial | runner | 🆕 Low-Med |
| TC-IM-B03 | TOCTOU disabled→claim returns the **wrong 409** (`SyncAlreadyRunning` instead of `ConnectorDisabled`) | Adversarial | runner | 🆕 Low |
| TC-IM-B04 | Stale-claim window (300 s) → a second trigger reclaims a still-live run → **two concurrent runners** double-count | Concurrency | runner | 🆕 Low |
| TC-IM-B05 | Delete during active sync → CASCADE purge, runner aborts cleanly (no orphans, no 500) | Adversarial | runner | ✅ |
| TC-IM-B06 | Cursor resume + UIDVALIDITY change → re-scan from UID 1, never advance past the stale floor | Positive | runner | ✅ |
| TC-IM-B07 | Never advance past a requested-but-unreturned UID (dropped FETCH = gap, not loss) | Positive | runner | ✅ |
| TC-IM-B08 | **Non-dedup IntegrityError / generic error on one UID → `tracker.fail` → cursor steps over it FOREVER** (never-lose-mail breach via a misclassified transient error) | Adversarial | runner | 🆕 Med |
| TC-IM-B09 | `failed_uids` ARRAY grows unbounded (cleared only on UIDVALIDITY reset) | Boundary | runner | 🆕 Low |
| TC-IM-B10 | Fence bypass: corrupt `sync_run_id` mid-run → both the live run and a new claimant miss; probe the `running`+`sync_run_id IS NULL` inconsistent state | Adversarial | runner | 🆕 Low |

### Suite C — Parse & data quality (`pure` / `ingest`)
| ID | Break hypothesis | Type | Mode | Exp. |
|---|---|---|---|---|
| TC-IM-C01 | **Deep-nested multipart (~250+) → `RecursionError`** — violates the "never raises" contract; uncaught in the service path; in the runner → silently `failed` + UID stepped over forever | Fuzz | pure+runner | 🆕 Med |
| TC-IM-C02 | **Dedup poisoning**: a forged Message-ID equal to a real email's → the genuine email is silently SKIPPED | Adversarial | ingest | 🆕 Med |
| TC-IM-C03 | **Direction spoof**: `From:` == mailbox address (no SPF/DKIM) → inbound attacker mail recorded as `outbound` (sent by the owner) | Adversarial | pure | 🆕 Low |
| TC-IM-C04 | **Control-char survival**: C0 chars except NUL (`\x01`, `\x1b` ANSI) survive into `subject`/`body_text`/`headers` (log/terminal injection) | Fuzz | pure+ingest | 🆕 Low |
| TC-IM-C05 | No size limit anywhere → an oversized email/attachment is fully materialized + sha256'd (resource/DoS) | Boundary | pure | 🆕 Low |
| TC-IM-C06 | Uncapped JSONB `headers` + `references` array → row bloat (10 MB header, 50k refs) | Boundary | pure | 📋 (CA-CONN-05) / 🆕 Low |
| TC-IM-C07 | Charset lie / unknown charset / RFC 2047 bomb → decode-with-replacement, never raises | Fuzz | pure | ✅ |
| TC-IM-C08 | Forged / missing Date with no INTERNALDATE → `received_at` from attacker headers → mis-orders `list_for_org` | Adversarial | pure | 🆕 Low |
| TC-IM-C09 | **Regression — 7c90f55 holds**: over-255 Content-Type capped, NUL in text-attachment stripped → email still ingests (no silent drop) | Negative | ingest | ✔ |
| TC-IM-C10 | NUL in subject/body/Message-ID → stripped / forces hash-dedup, no insert crash | Fuzz | pure+ingest | ✔ |

### Suite D — Attachments (`pure`)
| ID | Break hypothesis | Type | Mode | Exp. |
|---|---|---|---|---|
| TC-IM-D01 | Zip-bomb / decompression-bomb attachment → **INERT today** (binary extraction deferred; bytes dropped) — gating risk for CA-CONN-04 | Adversarial | pure | 📋 (CA-CONN-04) |
| TC-IM-D02 | Billion-laughs / XXE in an `.xml` attachment → **INERT** (`application/xml` is raw-decoded, no XML parser instantiated) | Adversarial | pure | 📋 (CA-CONN-04) |
| TC-IM-D03 | PDF/Office with macro/exploit → **INERT** (returns None, bytes never opened) | Adversarial | pure | 📋 (CA-CONN-04) |
| TC-IM-D04 | Encoding-lie attachment (binary mislabeled `text/plain`) → replacement-char soup in `extracted_text`, no crash | Fuzz | pure | 🆕 Info |
| TC-IM-D05 | Filename path-traversal `../../etc/passwd` → stored as a string, never used as a path → **LATENT** | Adversarial | pure | 📋 (CA-CONN-04) |

### Suite E — Persistence, RLS & entity graph (`db-rls` / `ingest`)
| ID | Break hypothesis | Type | Mode | Result |
|---|---|---|---|---|
| **TC-IM-E01** | **Live cross-tenant SELECT/INSERT/UPDATE on `person`/`person_email` as `oneai_app`** — does RLS actually bite on the entity graph? | Adversarial | db-rls | **✅ ✔ PASS (executed)** |
| TC-IM-E02 | Same, on `email_message`/`email_recipient`/`email_attachment` (seed a connection + email per org) | Adversarial | db-rls | ✅ ✔ |
| TC-IM-E03 | Entity over-merge: same display name, different person/company → two distinct people (no name matching exists in v1) | Positive | ingest | ✅ |
| TC-IM-E04 | Generic free-mail domain (`gmail.com`) → person created, **no company**; subdomain `x@mail.gmail.com` slips the skip-list (under-guard) | Boundary | ingest | ✅ / ⚠️ |
| TC-IM-E05 | Role/shared address (`info@`, `info+x@`) → no person; a non-listed role local-part (`enquiries@`) mints a person (list boundary) | Boundary | ingest | ✅ / ⚠️ |
| TC-IM-E06 | Whitespace-embedded address (`info @x.com`) dodges the role/empty-domain guard while still inserting a recipient | Adversarial | ingest | 🆕 Low |
| TC-IM-E07 | GUC must not leak across pooled txns: interleave two orgs on the tenant engine | Concurrency | db-rls | ✅ |
| TC-IM-E08 | The **ingest path + dump driver run on the BYPASSRLS global engine** — a fail-open surface until ingest moves to the tenant engine | Adversarial | ingest | 🆕 Med (flag) |
| TC-IM-E09 | person↔company link + concurrent person create race resolve via SAVEPOINT re-read (no dup, no abort) | Concurrency | ingest | ✅ |

## Status dashboard
> Consolidated audit: **`docs/audits/2026-06-09_imap-connector-dynamic-adversarial.md`**. Result legend: ✅ held · ⚠️ pass-with-concern · ❌ fail. The four B-suite ⚠️ rows (B03/B08/B09/B10) carry a NEW concern and are normalized to ⚠️ for table coherence (the suite agent logged them "Pass"); C01/C02 are ❌ (silent mail loss on input they must handle).

| ID | Result | Tag | Severity | Note |
|---|---|---|---|---|
| TC-IM-A01 | ✅ Pass | — | — | AES-256-GCM authenticates every blob: wrong-key / tag-flip / truncation each raise `ConnectorSecretError`, never garbage |
| TC-IM-A02 | ⚠️ Pass-concern | 🆕 NEW | Info | No AAD → ciphertext portable across rows/orgs (proven at cipher + DB level); not exploitable today (RLS+`get_in_org`), defense-in-depth gap |
| TC-IM-A03 | ✅ Pass | — | — | `extra='forbid'` → body `org_id` and out-of-enum `connector_type` both 422; org comes only from the JWT |
| TC-IM-A04 | ✅ Pass | — | — | **Non-negotiable held:** org B → A's id on GET/test/disable/enable/DELETE all 404, no A-data, A's row survives B's delete |
| TC-IM-A05 | ✅ Pass | — | — | `ConnectionResponse` is a non-secret allow-list; submitted password never echoed; weak key → 503, key not echoed |
| TC-IM-A06 | ✅ Pass | — | — | `require_company_admin` on every route: member → 403, no-token → 401 |
| TC-IM-A07 | ✅ Pass | — | — | 8-way duplicate create → one 201, seven 409, zero 5xx, one row; 409 from the `uq_connector_connection_identity` backstop |
| TC-IM-A08 | ⚠️ Pass-concern | 🆕 NEW | Low | No decrypt-by-version → rotating `CONNECTOR_SECRET_KEY` silently bricks every connection's decrypt; fail-shut, no leak; untracked in FIX_BEFORE_PROD |
| TC-IM-B01 | ✅ Pass | — | Info | Two concurrent POST /sync → [202, 409], one `running` row; clean "already running" detail, no 500 |
| TC-IM-B02 | ⚠️ Pass-concern | 🆕 NEW | Low | Disable mid-run does NOT stop an in-flight sync (`_sync` never re-checks `disabled_at`); design gap, not corruption |
| TC-IM-B03 | ⚠️ Pass-concern | 🆕 NEW | Low | TOCTOU disable→claim raises `SyncAlreadyRunning` instead of `ConnectorDisabled` — misleading 409 in a narrow race; no leak/loss |
| TC-IM-B04 | ⚠️ Pass-concern | 🆕 NEW | Low | Predicted double-count did **NOT** reproduce (fence + dedup held count at 2); residual = wasted-work window if STALE_SECONDS < batch time |
| TC-IM-B05 | ✅ Pass | — | Info | Delete mid-run cascade-purges email + cursor; resumed runner's fenced writes hit nothing; zero orphans |
| TC-IM-B06 | ✅ Pass | — | Info | New UIDVALIDITY resets the cursor floor to 0 → advances to the new generation's real high-water, never the stale floor |
| TC-IM-B07 | ✅ Pass | — | Info | Requested [1,2,3] / returned [1,3] stops the cursor at UID 1 (gap at 2, retried next run); contiguous-prefix advance |
| TC-IM-B08 | ⚠️ Pass-concern | 🆕 NEW | **Med** | Misclassified transient error → UID in `failed_uids` AND cursor steps past it → permanent silent mail loss; no retry budget |
| TC-IM-B09 | ⚠️ Pass-concern | 🆕 NEW | Low | `failed_uids` grows unbounded (length 50 over 50 runs), clears only on UIDVALIDITY reset; row bloat; pairs with B08 |
| TC-IM-B10 | ⚠️ Pass-concern | 🆕 NEW | Low | Corrupt `sync_run_id`→NULL wedges the connection ≤5 min (fail-safe); self-heals on STALE_SECONDS reclaim; needs an OOB write |
| TC-IM-C01 | ❌ **Fail** | 🆕 NEW | **Med** | ~300-deep multipart → `RecursionError` (breaks never-raises) → runner drops the email & finalizes `status=succeeded`; permanent silent loss |
| TC-IM-C02 | ❌ **Fail** | 🆕 NEW | **Med** | Reused Message-ID silently SKIPS a genuine email (`exists()` short-circuit); dedup trusts an attacker-influenceable header, no content hash |
| TC-IM-C03 | ⚠️ Pass-concern | 🆕 NEW | Low | `From == mailbox` → classified `outbound`; bare string compare, no SPF/DKIM/DMARC → "emails I sent" view poisonable |
| TC-IM-C04 | ⚠️ Pass-concern | 🆕 NEW | Low | C0 controls except NUL (`\x01`,`\x07`,`\x1b`) survive into stored subject/body; log/terminal-injection surface |
| TC-IM-C05 | ⚠️ Pass-concern | 🆕 NEW | Low | No size limit anywhere → 10 MB body + 8 MB attachment fully materialized + sha256'd; missing guardrail before prod fetch |
| TC-IM-C06 | ⚠️ Pass-concern | 🆕 NEW | Low | Unbounded `references` array (10k ids) is untracked → 🆕; the verbatim 1 MB `X-` header half is 📋 CONFIRMS-DOCUMENTED (CA-CONN-05) |
| TC-IM-C07 | ✅ Pass | — | — | Unknown charset / utf-8 lie / RFC 2047 bomb all decode-with-replacement, none raise; `errors='replace'` holds |
| TC-IM-C08 | ⚠️ Pass-concern | 🆕 NEW | Low | No INTERNALDATE + no Received → `received_at` from a forgeable `Date` (2099 pins to list top); prod INTERNALDATE neutralizes — fallback only |
| TC-IM-C09 | ✅ Pass | ✔ CONFIRMS-FIXED | — | Over-255 Content-Type capped + NUL in text attachment stripped → email STORES with 2 attachments; `7c90f55` holds |
| TC-IM-C10 | ✅ Pass | ✔ CONFIRMS-FIXED | — | NUL in subject/body/Message-ID stripped, no crash, no NUL in any column; mechanism corrected (NUL stripped before the dedup check) |
| TC-IM-D01 | ✅ Pass | 📋 CONFIRMS-DOCUMENTED | High† | Zip-bomb INERT — returns None, never decompressed; no zlib/gzip/zipfile imported (CA-CONN-04) |
| TC-IM-D02 | ✅ Pass | 📋 CONFIRMS-DOCUMENTED | High† | Billion-laughs/XXE INERT — `application/xml` raw-decoded, entities verbatim (out_len==in_len), no `root:` (CA-CONN-04) |
| TC-IM-D03 | ✅ Pass | 📋 CONFIRMS-DOCUMENTED | High† | PDF/Office/TNEF macros INERT — all five binary types return None, bytes never opened; benign text/plain control extracts (CA-CONN-04) |
| TC-IM-D04 | ⚠️ Pass-concern | 🆕 NEW | Info | Binary mislabeled `text/plain` → 513 U+FFFD replacement chars stored in `extracted_text`; Content-Type trusted, no sniffing |
| TC-IM-D05 | ✅ Pass | 📋 CONFIRMS-DOCUMENTED | High† | Filename `../../etc/passwd` stored verbatim but zero filesystem ops; LATENT until a binary store keys on the filename (CA-CONN-04) |
| TC-IM-E01 | ✅ Pass | ✔ CONFIRMS-FIXED | — | RLS holds live on `person`/`person_email` as `oneai_app`; 7/7; teeth (global sees both) + WITH CHECK reject confirmed |
| TC-IM-E02 | ✅ Pass | ✔ CONFIRMS-FIXED | — | RLS holds on `email_message`/`email_recipient`/`email_attachment`; 7/7; cross-org INSERT+UPDATE both rejected; confirms `0009` flip |
| TC-IM-E03 | ✅ Pass | — | — | Same display name, different addresses → exactly 2 persons (v1 matches on normalized email only; no name-merge tier) |
| TC-IM-E04 | ⚠️ Pass-concern | 🆕 NEW | Low | `mail.gmail.com` slips the exact-match `_GENERIC_DOMAINS` skip-list → mints a Company → free-mail-subdomain over-merge; org-scoped, recoverable |
| TC-IM-E05 | ⚠️ Pass-concern | — | Info | `info@`/`iNfO@`/`info+sales@` mint no person; `enquiries@`/`vertriebsteam@` do (intended under-exclusion); correct-by-design |
| TC-IM-E06 | ⚠️ Pass-concern | 🆕 NEW | Low | `normalize_email` doesn't strip internal whitespace → a non-`getaddresses` key dodges the role guard; **not reachable via the parser** |
| TC-IM-E07 | ✅ Pass | — | — | Org GUC strictly transaction-local: 3/3; no cross-pool bleed; post-txn unscoped read fails closed on `''::uuid` |
| TC-IM-E08 | ⚠️ Pass-concern | 🆕 NEW | **Med** | Prod (SyncRunner) RLS-enforced; but `EmailIngestService` is engine-agnostic and dev/test wires it to the BYPASSRLS engine → latent fail-open |
| TC-IM-E09 | ✅ Pass | — | — | Barrier-forced get-or-create race: SAVEPOINT re-read fired 5/5 (one won_insert + one lost_reread); 1 person_email, no abort |

† The High on D01/D02/D03/D05 is the **future gating risk** when CA-CONN-04's binary extractor lands — all four are inert/latent today, not active defects.

**Progress:** **42/42 executed** (all five suites A–E live; E01 pre-done). **2 ❌ Fails** — C01 (deep-multipart `RecursionError` → silent drop @ `succeeded`) and C02 (dedup-poison silently skips a genuine email), both Medium silent-mail-loss. **19 NEW findings: 4 Medium** (B08, C01, C02, E08) · **13 Low** · **2 Info** (A02, D04). **4 CONFIRMS-FIXED** (E01, E02, C09, C10) · **4 CONFIRMS-DOCUMENTED** (D01–D03, D05 — CA-CONN-04). **No cross-tenant exposure in any case;** the hardest rule (RLS) holds live on the email + entity tables. Full write-up: `docs/audits/2026-06-09_imap-connector-dynamic-adversarial.md`.
