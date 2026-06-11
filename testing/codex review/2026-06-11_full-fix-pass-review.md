DO-NOT-SHIP — I found two silent-mail-loss paths: TNEF-only content collisions and UIDVALIDITY-0 folders being skipped as successful work.

**Findings**
[HIGH] backend/app/connectors/imap/parsing/email_parser.py:253 — all `application/ms-tnef` attachments collapse to the literal key token `"tnef"`, so two distinct messages with the same Message-ID/from/subject/UTC Date/body/html and different `winmail.dat` semantic contents produce the same dedup key and the second is silently skipped — parse TNEF and hash stable semantic contents, or fall back to the decoded TNEF `content_hash` when semantic normalization is unavailable.

[HIGH] backend/app/connectors/imap/fetch_session.py:255 — server UIDVALIDITY `0` is mapped to `None`, and the fetcher treats `None` as “skip this folder”; a selectable folder on a nonconforming server can therefore drop all mail from that folder while the sync still succeeds — fail the folder/run loudly or persist a visible folder error/audit state instead of silently continuing.

[MEDIUM] backend/app/connectors/imap/parsing/email_parser.py:209 — the dedup identity excludes recipient headers and attachment metadata; same content plus reused Message-ID/date but different To/Cc/Bcc/Reply-To/Sender, or same attachment bytes with different filename/content-type/content-id, folds into one row — include a canonical recipient edge set and attachment metadata in the identity, or explicitly raw-fallback for reused-ID same-content envelope variants.

[MEDIUM] backend/app/identity/services/erasure_service.py:133 — erasure only fails closed when the registry is empty; a partially configured registry can erase identity data, run one hook, omit another PII domain, and still return a certificate — validate an exact required hook set such as `{"connectors", "entities"}` before any destructive work.

[MEDIUM] backend/app/entities/services/entity_resolver.py:327 — person `is_internal` compares exact domains even though company identity folds to eTLD+1; `alice@ibm.com` observed from mailbox `me@bg.ibm.com` can link to the same company but be marked external — compare `fold_to_registrable_domain(domain)` with `_mailbox_company_key`.

**Contested Decisions**
(a) TNEF presence-marker: reject as implemented. It optimizes duplicate folding by accepting a real silent-loss collision class.

(b) Headers-only raw-byte fallback: accept. It fails open to under-dedup, which is the safer side for empty-content messages.

(c) RLS-exempt erasure hooks with SQL-level org scoping: conditionally accept. The current hook DELETEs are org-scoped, but required-hook validation must be added.

(d) Dot-token exemption in role matcher: accept. It is the right bias for `firstname.lastname` human addresses.

(e) UIDVALIDITY-0 skip-folder: reject. It must be visible failure/degradation, not a silent skip.

**Missing Tests**
- TNEF no-loss test: same headers/body/date with different substantive TNEF payloads must not dedup.
- UIDVALIDITY-0 end-to-end sync test proving the run/folder is visibly failed or audited, not skipped.
- Partial erasure registry test: only `connectors` or only `entities` registered must fail closed.
- Erasure hook rollback test: first hook deletes, second hook raises, all deletes/org status/audit roll back.
- Dedup envelope test: reused Message-ID with different recipient headers on the same connection.
- Dedup attachment metadata test: same payload but different filename/content-type/content-id policy is explicit and tested.
- Entity internality test across folded domains, e.g. mailbox `me@bg.ibm.com` and participant `alice@ibm.com`.
---

## Reconciliation (Claude, 2026-06-11) — verdict per finding

| # | Finding | Codex sev | Verdict | Action |
|---|---|---|---|---|
| 1 | TNEF marker folds distinct emails differing only inside winmail.dat | HIGH | QUALIFIED (re-rated MEDIUM: needs Message-ID reuse + same-second instant + all else identical) | TNEF interior digest (attachments-only — 271/271 corpus groups stable; RTF body excluded as the instability source); tnefparse added; parse failure degrades to marker |
| 2 | UIDVALIDITY-0 → silent folder skip reported as success | HIGH | CONFIRMED | WARNING per sync naming the folder (test-asserted); run-ledger folder health tracked as CA-CONN-07. Codex's fail-the-run alternative rejected: one nonconforming folder must not block the other folders' ingestion |
| 3 | Dedup key ignores recipients + attachment metadata | MEDIUM | QUALIFIED (Bcc must be EXCLUDED — Sent copy asymmetry; Codex's as-stated fix splits copies) | Canonical To/Cc envelope + filename digest in the key; Bcc-asymmetry fold test |
| 4 | Erasure fails closed only on EMPTY registry | MEDIUM | CONFIRMED | REQUIRED_ERASURE_HOOKS completeness validation + partial-registry and rollback-atomicity tests |
| 5 | is_internal exact-compares while company identity folds | MEDIUM | CONFIRMED (entity_resolver.py:327 vs the folded _mailbox_company_key) | Internality folds both sides; bg.ibm.com/ibm.com test |

Contested designs Codex ENDORSED (independent corroboration): headers-only raw fallback, RLS-exempt hooks with SQL-level org scoping, dot-token role-matcher exemption. Verified after fixes: 631 backend tests green, 94.12% coverage; re-ingest identical to v3 (8,386/5,249/0; residual 18 rows).
