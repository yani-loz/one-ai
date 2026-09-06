# EPIC PF-01 — Permission-faithful within-tenant retrieval (ACL-at-ingest + grafts)

| Field | Value |
|---|---|
| **Epic ID** | PF-01 |
| **Module** | Permission Fidelity (`PF`) — new `access` module + extensions to `core`, `connectors`, `entities` |
| **Status** | 🔨 **BUILT (core) 2026-07-04, COMMITTED** as `db1795d` (55 files, 0019 + `app/access` + `tests/access`) — no longer "pending commit". Still uncommitted as of 2026-09-06: the R5 write-plane follow-up (`grant_writer.py`, `email_projection.py`, `test_grant_writer.py`) and migration `0023_reader_bcc_and_seen_window.py`, which exists on disk but is in no git object and is **unapplied** (dev DB `alembic_version` = `0022_counterparty_summary_v3`, measured 2026-09-06 — `docs/audits/2026-09-06_built-vs-docs-map.md` §3). Per-AC state in the Build-status addendum below. Spec agreed 2026-06-09 (Design A + 8 grafts from B/C) |
| **Branch** | `feat/permission-faithful-retrieval` was **proposed but never created** — PF-01 landed straight on `main` (`git branch --contains db1795d` → `main`, `ask-tools-loop`; verified 2026-09-06) |
| **PR** | — (PR-PF-1: migration 0019 + GUC seam + grant writers + invariant tests) |
| **Depends on** | Migration 0009 (`oneai_app` NOSUPERUSER / FORCE RLS / GUC seam), 0007–0011 (IMAP tables incl. `email_recipient`), 0012 (entity spine `person`/`person_email`), PC-04 (`audit_log` append-only), PC-06 (erasure-hook discipline), **CA-CONN-05 full-headers retention review — precondition: header retention must be reconciled with the grant model before email grants go live (owner-copy projection, AC19)** |
| **Closes (FIX_BEFORE_PROD)** | "Per-mailbox/per-source access control — tracked separately" (ingestion design §7); "connector-lifecycle visibility must be enforced in every retrieval path" |
| **Review** | — (adversarial design pass done: A vs B vs C; A selected, B/C rejected wholesale, 8 grafts absorbed) |
| **Date** | 2026-06-09 |
| **Estimate** | ~3–5 dev-days (≈ 24–40 h of spec-driven AI dev). **Gate: `chunk` and every retrieval table is built only AFTER this lands — no retrieval layer ever exists un-scoped.** |

## 0. Build-status addendum (2026-07-04 — what shipped vs. what waits)

Core build landed as migration `0019_permission_fidelity` + `backend/app/access/` (models,
grant writer, promotion service+route, source-identity + erasure repositories, decision
telemetry, AC19 projection), `reader_session(org_id, person_id)` — the person-bound seam is
`reader_session`, **not** `scoped_session`, which takes `org_id` only
(`backend/app/core/database.py:105,127`) — + the engine-seam assertion in
`core/database.py`, GrantWriter wired into `EmailIngestService`. Tests: `tests/access/` (30
green when this addendum was written; **45 test functions across 10 modules** in the working
tree counted 2026-09-06 — but the newest evidence is **not committed**:
`test_counterparty_summary.py` (4 functions, an ASK/0022 deliverable) is intent-to-add, and
`test_grant_writer.py` carries 89 uncommitted insertions holding the three R5/PF-FBP-14 tests
that AC10 below cites — `test_ingest_order_does_not_decide_who_can_read_the_message`,
`test_a_bcc_header_never_mints_a_grant`, `test_the_backfill_script_derives_the_same_kinds_as_ingest`.
Not re-run in this pass) + the INV extensions. Per-AC state:

| State | ACs | Note |
|---|---|---|
| ✅ proven by tests | AC1, AC2, AC3, AC5, AC8, AC10, AC13, AC15, AC16, AC18, AC19, AC21 | `tests/access/*` + `backend/tests/identity/models/test_rls_invariants.py::test_every_content_table_has_org_isolation_and_visibility_policy` (real path re-located 2026-09-06; §4 below now carries the same ✅ marks and the actual shipped test names) |
| ✅ partially / reframed | AC14 (tombstone-hides ✅; 404-purge waits for a connector with a 404 signal), AC17 (INV auto-covers the 4 new tables), AC20 (verified-only login binding ✅; merge-guard waits for the CA-CONN-09 merge tier), AC22 (children-bind-to-parent ✅ on today's children; chunk/fact placeholder stands) | residuals in FIX_BEFORE_PROD PF-FBP-6..9 |
| ⬜ deferred with a named owner | AC4 (no retained 'disconnected' state exists today — disconnect IS a cascade delete; predicate lands with such a state), AC6 (schema shipped; unreadability enforceable only when a fact table exists), AC7+AC11 (Slack capture/re-check — with the Slack connector), AC9 (chat layer — PF-FBP-2), AC12 (local-folders connector) | FIX_BEFORE_PROD PF section |

Three implementation decisions taken during the build (all fail-closed, all documented in the
migration docstring):

1. **THE READER PLANE — §2's back-pocketed fourth role is now LOAD-BEARING.** Discovered live:
   PostgreSQL applies SELECT policies to rows returned by `INSERT ... RETURNING`, so a
   restrictive visibility policy on the write role breaks person-less ingest inserting the very
   restricted rows it creates (every ORM insert RETURNINGs server defaults). One role cannot be
   both the person-less write plane and the person-scoped fail-closed read plane. Resolution:
   `oneai_reader` (NOSUPERUSER, NO BYPASSRLS, **SELECT-only** — plus INSERT on audit_log for
   AC16 telemetry); the `visibility` policies target it; `core.reader_session(org, person)` is
   the retrieval seam (AC3/AC18 now hold ON THE READER PLANE, and the agent/retrieval plane
   physically cannot write — stronger than the spec's single-role design). `oneai_app` keeps
   org-RLS-only visibility: trusted first-party write/system code, within-tenant rules enforced
   in services (ownership checks), exactly like the CO-01 admin plane. Promotion accordingly
   runs on the app plane (a write flow); the reader plane is for serving content.
2. Ingest dedup gained a savepoint-guarded insert (idempotency independent of any read-
   visibility regime — belt for concurrent syncs).
3. Email capture grants `sender` provenance to the From author alongside recipients + owner
   (the author could see it at source — a strict fidelity improvement over recipients-only).

**2026-07-04 same-day review triage (15 findings + 5 P-items; all verified against source
before acting):** FIXED — reader-plane split (the Critical RETURNING finding + the reader
audit-INSERT RETURNING grant), on-skip grant RECONCILIATION in ingest (re-ingest is now the
production reconciliation caller AND the pre-0019 corpus's grant backfill), depth-tagged
resolution-cache staging (savepoint-scoped discard), telemetry actor-id/person-id id-space
separation, email origin_scope CHECK-pinned to 'restricted' (closes the born-org lineage
side door), promotion row-lock + guarded flip (no double 201), visibility_promotion
UPDATE+DELETE revoked from the tenant role (append-only as a privilege), owner lookup moved
to the repo layer, single principal derivation with owner>sender>recipient precedence, xlsx
grid reuse round-trip test, served-headers⊆stored-allowlist drift guard, and the cross-plane
end-to-end test (ingest on oneai_app → grants → serve on oneai_reader → telemetry).
ACCEPTED-AS-TRACKED — no production verified-identity WRITER yet (PF-FBP-7; UNKNOWN⇒DENY is
the designed posture until bindings exist). REFUTED — "savepoint rollback poisons the batch
today" (no production begin_nested wraps an email; hardened anyway), "projection duplicates
the header allowlist" (deliberately narrower set, different question; subset now drift-
guarded). SKIPPED — constraint-name string-match (consistent with the existing driver
pattern).

## 1. Goal & context

Org-level RLS (migration 0009) guarantees **tenant** isolation. It says nothing about
**within-tenant** access: today, the moment a `chunk` table exists, every employee's AI could
retrieve every other employee's private email, DM, and local file. PF-01 closes that hole
**before** the retrieval layer is born.

**The invariant (non-negotiable):**

> **A user's AI may retrieve a piece of content only if that user could see it in the source
> system. The authorization *decision* is an RLS predicate evaluated by the existing
> `oneai_app` role (NOSUPERUSER, no BYPASSRLS, FORCE RLS) on every content table — the same
> static-pool guarantee as org isolation. Tool code, future agent SQL/REPL, hallucinated tool
> calls, and buggy filters CANNOT widen results, because the role cannot.**

*As built (noted 2026-09-06, the invariant itself unchanged): the enforcing role in that
paragraph is **`oneai_reader`**, not `oneai_app` — see §0 decision 1. The `visibility` policies
are RESTRICTIVE SELECT policies granted to `oneai_reader`, the SELECT-only retrieval plane
(3 such policies live on the dev DB, measured 2026-09-06,
`docs/audits/2026-09-06_built-vs-docs-map.md` §3); `oneai_app` carries org-level RLS only and
is the write/system plane. On the reader plane the guarantee is strictly stronger than written
here: that role holds no write privilege on content at all. Every `oneai_app` reference below
that predates the build should be read through this correction.*

**The invariant holds only if every tenant/retrieval query actually runs on the `oneai_app`
pool.** The `oneai_global` engine is BYPASSRLS, and a tenant flow that mistakenly acquires it
fails **open and silent** — bypassing org AND person RLS at once, across every org. Engine
selection is therefore itself fail-closed **by construction, not convention**: the
`access`/retrieval code path has no import path to `global_engine` (module-import guard), and
`scoped_session` aborts unless `SELECT current_user` returns `oneai_app` (runtime assertion).
Both are tested structurally (AC18); until AC18 is green this is a **FIX_BEFORE_PROD blocker**,
not an asserted property.

Design A (ACL-at-ingest, RLS-enforced) is the architecture. Designs B (live source re-check as
primary) and C (private-by-default everything) are **rejected wholesale** — B puts source APIs
on the query path and trusts caches as authority; C is product-fatal for MVP memory richness.
Eight grafts from B/C are absorbed where they only *narrow* (see §6).

> **The hard truth this epic confronts:** permission capture at ingest is a **snapshot** — the
> source's ACL can drift (member removed from a channel, file unshared) between syncs. We do
> not pretend otherwise. The DB grant is the **floor**; a targeted live re-check may only
> **narrow** it; sync-staleness alerting and revocation propagation are explicit, tracked
> obligations (§7 / FIX_BEFORE_PROD), not silent assumptions. Likewise, **widening** an
> audience is never an emergent side effect: it is impossible by construction without an
> append-only `visibility_promotion` row carrying a human approver and an audit-log link —
> the artifact a DACH works council asks for.

## 2. Scope

**In scope (PF-01 — migration 0019 + enforcement + capture + tests):**

*(The spec drafted this as "0013"; the slot was taken by `0013_least_privilege_grants.py` and
PF-01 shipped as `0019_permission_fidelity.py` — corrected throughout on 2026-09-06.)*

- **Data model (migration 0019):** `acl_grant`, `visibility_promotion`,
  `principal_source_identity`, `fact_provenance` (schema now, populated by the Learn layer),
  plus `visibility_scope`/`origin_scope` (immutable, set at ingest)/`container_id` columns on
  all content/chunk tables (§ "Data model").
- **Enforcement:** `reader_session(org_id, person_id)` — second GUC `app.current_person_id`
  set in the same `after_begin` listener; per-table `visibility` RLS policies (**as built**
  these are RESTRICTIVE SELECT policies targeting `oneai_reader`, not `oneai_app` — see §0
  decision 1; live DB shows 3 × `visibility` RESTRICTIVE/SELECT/`{oneai_reader}`, measured
  2026-09-06, `docs/audits/2026-09-06_built-vs-docs-map.md` §3);
  connector-lifecycle predicate (pause ≠ disconnect) inside the policy; **engine-seam guard**
  (`scoped_session` aborts unless `current_user = 'oneai_app'` and `reader_session` unless
  `oneai_reader`; the `access`/retrieval package has no import path to `global_engine`).
- **Per-connector permission capture at ingest:** email (per-message grants from
  `email_recipient` + connection owner; non-owner grant-holders are served a **redacted
  projection of the recipient's source-view** — never the BCC set or owner-only headers),
  Slack (public → `org`; private/DM → container grants
  from membership snapshots), local folders (owner-only).
- **Conservative defaults:** **UNKNOWN ⇒ DENY** and **SHRINK ⇒ TOMBSTONE** (sync-time grant
  reconciliation: removed principals are tombstoned, not just absent from new grants) as
  *named rules*; owner-driven widening via a
  one-click promotion-candidate queue (v1 review-queue pattern) feeding `visibility_promotion`.
- **Defense-in-depth:** targeted live re-check for restricted Slack hits (narrow-only,
  cached, TTL 60–300 s); 404-driven purge through the existing `FOR UPDATE SKIP LOCKED`
  work-queue; retrieval decision telemetry into the append-only `audit_log` (resource keys,
  never content) + reduced-coverage disclosure in answers.
- **Revocation/deletion propagation:** tombstone semantics (`revoked_at`), sync-time
  reconciliation for **shrink-without-deletion** (un-shared folder, narrowed membership — no
  404 ever fires), purge queue, and
  wiring of all four new tables into the **erasure-hook registry** with explicit
  scrub-vs-retain decisions per table.
- **Standing-invariant tests:** `test_rls_invariants.py` extensions (dynamic enumeration,
  FAIL never SKIP) — see AC matrix.

**Out of scope (later):**

- The retrieval layer itself (`chunk` table, embeddings, BM25, hybrid search, AgentLoop,
  tools) — **deliberately sequenced after PF-01** so it is born scoped. **The chunk→grant
  binding contract, however, is specified HERE (§6 + AC22), not left to the later epic** —
  the join semantics between a chunk and its parent's grants are load-bearing and cannot be
  an implementation detail of the epic this one gates.
- The Learn layer's *population* of `fact_provenance` (schema + invariants ship now; Step 7
  writes the rows). Audience-intersection vs Learn-layer fact synthesis gets **its own design
  pass** (FIX_BEFORE_PROD register, deferred deliberately).
- Nightly Board system principal — gets **break-glass treatment before Step 7**: dedicated
  role, mandatory logged-session seam, never a silent BYPASSRLS-equivalent (FIX_BEFORE_PROD).
- B's fourth-role column-privilege trick — back pocket **only if** a non-RLS retrieval path
  ever appears (none is planned).
- Any source API on the **general** query path (rejected with Design B); live re-check stays
  narrow (restricted Slack hits only).
- `admin_widen` as a grant provenance — **removed from the enum**; admins do not widen
  audiences, owners do, through `visibility_promotion`.
- Google Drive / SharePoint / other connectors' permission capture — same pattern
  (`principal_source_identity` + container grants), specced per-connector when each lands.

## 3. User stories

| ID | Story |
|---|---|
| PF-01-S1 | As an **employee**, my AI answers only from content I could see at the source — never from a colleague's mailbox, a private channel I'm not in, or someone else's local folder. |
| PF-01-S2 | As a **mailbox/file owner**, I can widen something to the whole org with one click from a promotion-candidate queue — and that widening is mine, recorded, and auditable. |
| PF-01-S3 | As a **DPO / works council**, every audience widening has an append-only `visibility_promotion` row with a named human approver and an audit-log link — there is no other widening path. |
| PF-01-S4 | As a **company admin**, I control connections and roles but cannot read restricted content I have no grant for — admin ≠ omniscient. |
| PF-01-S5 | As **the system**, every ambiguity fails closed: unknown principal, unverified identity, missing GUC, missing provenance, dead connector — the row is simply not visible. |
| PF-01-S6 | As a **user who deletes/un-shares at the source**, One AI converges: revocation tombstones the grant, 404s purge the content, and replayed history is re-filtered. |

## 4. Acceptance criteria → tests (traceability matrix)

> ⭐ = compliance/security-critical. INV = `backend/tests/identity/models/test_rls_invariants.py`
> (dynamic enumeration — **FAILs, never SKIPs**; the spec's `backend/tests/test_rls_invariants.py`
> path never existed); PF = `backend/tests/access/`.
>
> **Status reconciled 2026-09-06.** This table used to read "All rows ⬜ until the PR" while §0
> already marked twelve of them ✅ — the two halves of the file contradicted each other. A row is
> now ✅ **only** where its proving test was located in the working tree on 2026-09-06; several
> tests were renamed or moved between planes during the build and are re-pointed inline, and
> names that resolve to nothing are called out as such. The tests were **not re-run** in this
> pass (running `pytest` truncates the dev corpus). Rows still ⬜ are unbuilt, deferred, or only
> partially proven — §0 says which.
>
> Tests run on the enforced roles — `oneai_app` (write plane) and `oneai_reader` (the visibility
> tests, through `reader_session`; `test_visibility_policies.py:36`) — never the owner engine.

| AC | Criterion | Proven by |
|---|---|---|
| ✅ ⭐ PF-01-AC1 | **Dynamic enumeration:** every content table carries BOTH an `org_isolation` policy AND a `visibility` policy; a content table without a visibility policy is a **CI FAIL**. | INV `::test_every_content_table_has_org_isolation_and_visibility_policy` (exists at `backend/tests/identity/models/test_rls_invariants.py:196` — the only ✅ row whose proof lives outside `tests/access/`) |
| ✅ ⭐ PF-01-AC2 | **Per-table negative test:** for each content table, a principal with no grant sees **zero** `restricted` rows (parametrized over the enumerated tables). | PF `test_visibility_policies.py::test_granted_person_sees_restricted_email_ungranted_sees_zero` , `::test_children_bind_to_the_parents_grants` — **shipped narrower than the criterion:** covers `email_message` + its children (`email_recipient`, `email_attachment`), **not parametrized over every enumerated content table**; no test named `::test_ungranted_principal_sees_zero_restricted_rows[table]` exists (2026-09-06) |
| ✅ ⭐ PF-01-AC3 | **GUC fail-closed:** `app.current_person_id` unset → predicate NULLs → only `visibility_scope='org'` rows visible; no error path leaks restricted rows. | PF `test_visibility_policies.py::test_person_guc_unset_yields_org_scope_rows_only` (same name, shipped under PF rather than INV) |
| ⬜ ⭐ PF-01-AC4 | **Lifecycle:** `disconnected` connection → rows hidden by policy at next query; `paused` → rows stay visible. Pause ≠ disconnect, enforced in the predicate, not in tool code. | INV `::test_disconnected_connector_hides_rows` , `::test_paused_connector_keeps_rows_visible` |
| ✅ ⭐ PF-01-AC5 | **Widening only via promotion:** every row with immutable `origin_scope='restricted'` and current `visibility_scope='org'` has `visibility_promotion` lineage with `approved_by_user_id` NOT NULL and `audit_log_id` NOT NULL; the lineage-guard trigger fires on **both INSERT and UPDATE** — direct `UPDATE … SET visibility_scope='org'` AND direct `INSERT` of a restricted-origin row as `'org'` without lineage are both rejected. `origin_scope` is set once at ingest and immutable thereafter (born-org rows, e.g. public Slack, pass the guard legitimately). | PF `test_visibility_policies.py::test_update_widening_without_promotion_row_is_rejected` , `::test_insert_org_row_of_restricted_origin_without_lineage_is_rejected` , `::test_origin_scope_is_immutable` ; PF `test_promotion.py::test_email_content_cannot_be_born_org_visible` , `::test_promotion_history_is_append_only` . *(Re-pointed 2026-09-06: the three trigger tests shipped in `test_visibility_policies.py`, not `test_promotion.py`; no INV test named `::test_org_rows_of_restricted_origin_have_promotion_lineage` exists.)* |
| ⬜ ⭐ PF-01-AC6 | **Fact provenance fail-closed:** a fact without a `fact_provenance` row is unreadable; provenance shrink (erasure, grant revocation, scope suspension) flips `status='quarantined'` and the fact stops resolving. (Synthetic rows until Step 7 populates.) | INV `::test_fact_without_provenance_is_unreadable` , `::test_provenance_shrink_quarantines_fact` |
| ⬜ ⭐ PF-01-AC7 | **Live re-check narrows only — bounded:** for restricted Slack hits, result set after re-check ⊆ DB-granted set; **transient** cache/API failure ⇒ DB floor stands (UNKNOWN ⇒ floor, never widen, never error-open) — but only up to a **hard staleness TTL**: a restricted Slack grant whose membership snapshot cannot be refreshed within the TTL is **suspended** (fail-closed) until a fresh snapshot lands. The floor does not stand forever through an outage. | INV `::test_live_recheck_result_is_subset_of_db_granted_set` ; PF `test_live_recheck.py::test_recheck_failure_falls_back_to_db_floor` , `::test_stale_membership_snapshot_suspends_grant_after_ttl` |
| ✅ ⭐ PF-01-AC8 | **Unverified identity ⇒ no grant:** an `acl_grant` whose principal resolves only through an unverified `principal_source_identity` never matches; ingest writes no grant for unmappable principals (UNKNOWN ⇒ DENY as a named rule). | PF `test_grant_writer.py::test_unmappable_principals_write_no_grant` , `::test_unverified_identity_writes_no_grant` . *(Re-pointed 2026-09-06; no INV test named `::test_unverified_source_identity_yields_no_grants` exists.)* |
| ⬜ ⭐ PF-01-AC9 | **History-replay re-filter:** multi-turn history replay re-filters revoked/quarantined content before context assembly — revoked content cannot re-enter via conversation history. Until the chat layer exists: invariant written, marked FIX_BEFORE_PROD. | INV `::test_history_replay_refilters_revoked_content` (FIX_BEFORE_PROD until chat layer lands) |
| ✅ PF-01-AC10 | **Email capture:** ingest writes per-message grants for every `email_recipient` (to/cc/bcc/reply_to/sender, resolved via verified identity) + the connection owner; an org member who was not a recipient sees nothing. **A grant on another owner's copy is a grant to a redacted projection, not the raw row** (see AC19) — a recipient's own connected copy is the authoritative source for their full view. | PF `test_grant_writer.py::test_verified_recipients_and_owner_get_per_message_grants` , `::test_ingest_order_does_not_decide_who_can_read_the_message` , `::test_a_bcc_header_never_mints_a_grant` ; non-recipient case → `test_visibility_policies.py::test_granted_person_sees_restricted_email_ungranted_sees_zero` . **The criterion's "to/cc/bcc/reply_to/sender" clause is superseded by what shipped:** only disclosed recipients mint grants — `DISCLOSED_RECIPIENT_KINDS = frozenset({"to", "cc"})` (`backend/app/connectors/imap/parsing/email_parser.py:142`, the PF-FBP-14 R5 fix, **uncommitted** as of 2026-09-06). Amending the criterion text is a founder call, not a doc-maintenance one |
| ⬜ PF-01-AC11 | **Slack capture:** public channel rows ingest as `visibility_scope='org'`; private/DM rows ingest `restricted` with **container** grants from the membership snapshot; a non-member sees zero rows from that container. | PF `test_grant_writer.py::test_slack_public_is_org_scope` , `::test_slack_private_container_grants_match_membership` |
| ⬜ PF-01-AC12 | **Files capture:** local-folder content ingests owner-only; widening happens exclusively through the promotion queue. | PF `test_grant_writer.py::test_local_folder_is_owner_only` |
| ✅ PF-01-AC13 | **Promotion queue:** owner sees promotion candidates, one click approves → `visibility_promotion` row + `audit_log` entry + scope flip in one transaction; non-owners cannot approve. | PF `test_promotion.py::test_owner_one_click_promotion_is_atomic_and_audited` , `::test_non_owner_cannot_promote` (both names resolve as written); HTTP-level cover in `test_promotion_routes.py::test_owner_promotes_over_http` , `::test_granted_non_owner_can_see_but_not_widen_403` |
| ⬜ ⭐ PF-01-AC14 | **Revocation & 404 purge:** grant revocation sets `revoked_at` (tombstone; partial UNIQUE keeps re-grant clean) and the row stops matching immediately; source-404 (sync or live re-check) tombstones + enqueues chunk/content purge via the `FOR UPDATE SKIP LOCKED` work-queue; queue drains to actual row deletion. **Deny-but-retain is not acceptable deletion fidelity.** | PF `test_revocation.py::test_tombstone_hides_immediately` , `::test_404_enqueues_and_purges_content` |
| ✅ ⭐ PF-01-AC15 | **Erasure wiring:** all four new tables registered in the erasure-hook registry (`identity` imports nothing from `access`/`connectors`); explicit scrub-vs-retain per table (§6); org-level erasure leaves no `acl_grant`/`principal_source_identity` PII behind. | PF `test_erasure_hooks.py::test_access_hook_is_required_and_registered_by_the_composition_root` , `::test_erasing_org_a_deletes_access_rows_and_spares_org_b` ; registry-completeness is asserted through `REQUIRED_ERASURE_HOOKS` (`test_erasure_hooks.py:36`, `backend/tests/identity/services/test_erasure_service.py:205`). *(Re-pointed 2026-09-06; neither `::test_access_tables_registered_and_scrubbed` nor an INV `::test_every_tenant_table_is_wired_into_erasure` exists under those names.)* |
| ✅ PF-01-AC16 | **Decision telemetry:** retrieval-time allow/deny counts + resource keys (never content) land in append-only `audit_log`; when filtering starves retrieval below a threshold, the answer path receives a reduced-coverage signal to disclose. | PF `test_decision_telemetry.py::test_decisions_are_logged_as_keys_never_content` , `::test_reduced_coverage_boundaries` (the reduced-coverage threshold, via `is_coverage_reduced`). *(Re-pointed 2026-09-06: the file shipped as `test_decision_telemetry.py`, not `test_telemetry.py`.)* |
| ⬜ ⭐ PF-01-AC17 | **Role floor unchanged:** `oneai_app` remains NOSUPERUSER, non-owner, no BYPASSRLS; FORCE RLS on every new table; `oneai_global` never receives the person GUC listener; tenant flows never run on the global engine — **asserted here, structurally enforced by AC18** (an assertion without a structural guard is convention, and the failure mode is silent + maximally permissive). | INV (existing, extended) `::test_role_attributes_and_force_rls[table]` |
| ✅ ⭐ PF-01-AC18 | **Engine seam fail-closed by construction:** the `access`/retrieval package has **no code path to `global_engine`** (module-import guard); `scoped_session` carries a runtime assertion that `SELECT current_user` returns `oneai_app` and aborts otherwise; a retrieval query attempted on the global engine is refused, not silently widened. **FIX_BEFORE_PROD blocker until green** — no agent/retrieval module starts before this passes. | PF `test_engine_seam.py::test_access_package_never_imports_the_global_engine` , `::test_scope_binding_aborts_on_the_wrong_role` , `::test_scoped_and_reader_sessions_run_as_their_expected_roles` . *(Re-pointed 2026-09-06: all three shipped under PF rather than INV; the assertion is per plane — `scoped_session` expects `oneai_app`, `reader_session` expects `oneai_reader` — `backend/app/core/database.py:_bind_scope`.)* |
| ✅ ⭐ PF-01-AC19 | **Owner-copy redaction (BCC leak):** non-owner recipient retrieval **never exposes BCC recipients or the full header chain** of another owner's copy; the served projection equals what that recipient could see in their own source copy (no BCC line, no `Received`/headers JSONB not present in a recipient copy, no owner-only folder/annotation context). | PF `test_email_projection.py::test_non_owner_never_sees_bcc_recipients` , `::test_non_owner_projection_matches_recipient_source_view` , `::test_served_headers_are_a_subset_of_the_stored_allowlist` (all names resolve as written) |
| ⬜ ⭐ PF-01-AC20 | **Login binding survives entity resolution:** the JWT→`person_id` binding is admin/IdP-controlled, `verified`, and **immutable by reviewer merges**; a `person` merge that would change an authenticated principal's grant surface is blocked pending explicit admin confirmation (or re-auth); an unverified or merged-ambiguous principal resolves **zero** restricted grants (extends AC8's ingest-side rule to the login side). | INV `::test_merged_or_ambiguous_principal_yields_zero_restricted_grants` ; PF `test_source_identity.py::test_merge_touching_authenticated_binding_is_blocked` |
| ✅ ⭐ PF-01-AC21 | **Grant reconciliation on sync (SHRINK ⇒ TOMBSTONE):** each sync **diffs** prior vs current recipient/container membership and tombstones (`revoked_at`) grants for removed principals — access removal at the source propagates **without requiring object deletion** (no 404 fires when a folder is un-shared or membership narrows). Grant writing is reconciliation, never append-only. | PF `test_grant_writer.py::test_reconciliation_tombstones_principal_whose_verification_was_revoked` , `::test_reingest_skip_path_reconciles_grants` , `::test_reconciliation_adds_newly_verified_principal` ; revocation-hides cover in `test_visibility_policies.py::test_revoked_grant_hides_rows_immediately` . *(Re-pointed 2026-09-06; no test named `::test_sync_reconciliation_tombstones_removed_principals` or `::test_membership_shrink_without_deletion_revokes_grant` exists — the container-membership half has no connector to exercise it, cf. AC11.)* |
| ⬜ ⭐ PF-01-AC22 | **Chunk/fact inherit the parent's grant surface:** for every future `chunk` row, the set of principals who can SELECT it **equals** the set who can SELECT its parent content row — the chunk carries the parent's `object_type`/`object_id`/`container_id` and inherits `visibility_scope`/`origin_scope` in the same transaction that creates it; same equivalence for facts vs `fact_provenance`. Until the chunk/fact tables exist: failing-placeholder invariant, FIX_BEFORE_PROD (same pattern as AC9). | INV `::test_chunk_principal_set_equals_parent_principal_set` , `::test_fact_principal_set_equals_provenance_principal_set` (FIX_BEFORE_PROD until chunk/fact land) |

## 5. Implementation map

| Area | Files (proposed) |
|---|---|
| Migration | `backend/app/db/migrations/versions/0019_permission_fidelity.py` — shipped, 515 lines, in `db1795d` (`acl_grant`, `visibility_promotion`, `principal_source_identity`, `fact_provenance`; `visibility_scope` DEFAULT `'restricted'` + immutable `origin_scope` (set at ingest, change-rejected by trigger) + `container_id` on content tables; visibility policies + FORCE RLS; lineage-guard trigger on **INSERT and UPDATE**; partial UNIQUE `(org_id, object_type, object_id, person_id) WHERE revoked_at IS NULL` + covering index) |
| Models | `access/models/{acl_grant,visibility_promotion,principal_source_identity,fact_provenance}.py` (all on `TenantMixin` — `org_id` NOT NULL + indexed) |
| GUC seam | `core/database.py` — as built, two seams: `reader_session(org_id, person_id)` (person-bound reads, asserts `oneai_reader`) and `scoped_session(org_id)` (write/system plane, asserts `oneai_app`); `after_begin` adds `set_config('app.current_person_id', …, true)` on **every** transaction (explicitly `''` when there is no person); both values derived **only** from the verified JWT / verified auth binding (never header/body); background jobs open their own scoped session; **engine-seam guard**: per-plane runtime assertion on `current_user` (abort otherwise) + module-import guard denying the `access` package any path to `global_engine` (AC18) |
| Grant capture | `access/services/grant_writer.py` (UNKNOWN ⇒ DENY and SHRINK ⇒ TOMBSTONE rules live here, single choke point — each sync diffs prior vs current recipients/membership and tombstones removed principals, AC21); called from `connectors/imap/services/ingest_service.py` (+ each future connector's ingest) |
| Email projection | `access/services/email_projection.py` (non-owner grant-holders get the redacted recipient source-view of another owner's copy: strip BCC recipients, header chain, owner-only folder/annotation context — AC19; CA-CONN-05 header-retention review is the precondition) |
| Identity mapping | `access/services/source_identity_service.py` (per-connector external id → `person_id`, `verified` flag; email-connector case = JWT-email mapping, same table, no separate mechanism; **auth-binding guard**: bindings for authenticated principals are immutable by reviewer merges — a merge touching one is blocked pending admin confirmation, AC20) |
| Promotion | `access/services/promotion_service.py` + `access/routes/promotion_routes.py` (candidate queue, one-click approve, atomic promote+audit) |
| Live re-check | `access/services/slack_recheck.py` (cached `conversations.members`, TTL 60–300 s, resource-group granularity, narrow-only, fail ⇒ floor) |
| Purge | `access/services/purge_service.py` + work-queue items on the existing `FOR UPDATE SKIP LOCKED` queue (404-driven tombstone → content/chunk purge) |
| Telemetry | `access/services/decision_telemetry.py` → `identity/services/audit_service.py` (allow/deny counts + resource keys; doubles as the staleness alert) |
| Erasure | `access/erasure_hooks.py` registered via the erasure-hook registry (no `identity` → `access` import) |
| Tests | `backend/tests/identity/models/test_rls_invariants.py` (extensions, dynamic enumeration — the real path; `tests/test_rls_invariants.py` never existed), `backend/tests/access/` (grant writer, promotion + routes, visibility policies, engine seam, email projection, decision telemetry, erasure hooks, end-to-end planes, counterparty summary) |
| Tracking | `docs/FIX_BEFORE_PROD.md` (new entries, §7) |

## 6. Decisions settled during the design sprint

- **A over B over C — and why:** the decision point is *where the authorization decision
  executes*. A puts it in an RLS predicate under a role that physically cannot widen (static
  pool property, same as org isolation). B's primary live re-check puts source APIs on the
  query path (latency, availability coupling, and the cache becomes the de-facto authority);
  C's private-by-default-everything kills the MVP's "rich shared memory from day one" promise
  (**product-fatal** — an org-wide AI that knows nothing shared is a worse ChatGPT). B and C
  rejected wholesale; eight grafts absorbed where they only narrow or add audit.
- **`admin_widen` removed from the grant-provenance enum.** The **only** widening path is
  `visibility_promotion` (graft from C): append-only, `approved_by_user_id` NOT NULL,
  `audit_log_id` NOT NULL. Local-folder widening today; Learn-layer fact promotion later. This
  is the works-council artifact: "show me every time something private became org-visible,
  who approved it, and when" is one SELECT.
- **Lineage guard fires on INSERT and UPDATE, discriminated by immutable `origin_scope`
  (corrected in review):** public Slack legitimately INSERTs `visibility_scope='org'` at
  ingest, so an UPDATE-only trigger cannot enforce "every org row of restricted origin has
  lineage" — buggy/malicious code could INSERT a restricted-origin row directly as `'org'`
  and never fire it. `origin_scope` (`org` born-org vs `restricted`) is set once at ingest
  and immutable; the guard rejects any row with `origin_scope='restricted'` and
  `visibility_scope='org'` lacking promotion lineage, on **both** statement paths (AC5).
- **`principal_source_identity` with a `verified` flag (graft from B):** per-connector
  external identity (email address, Slack user ID, Drive principal) → `person_id`.
  **Unverified ⇒ no grant resolves against it.** JWT-email mapping is the email-connector
  special case of this table — one mechanism, not two.
- **Login binding ≠ content entity resolution (corrected in review):** the entity resolver
  (deterministic + human-review merges, with its known ambiguity pit) must never silently
  rewire an authenticated user's grant surface — a false person-merge would fuse two
  employees' `acl_grant` sets into a cross-user ACL bypass. The JWT→`person_id` binding is
  therefore a **security-critical, admin/IdP-controlled mapping**: it must be `verified`, is
  immutable by reviewer merges, and any `person` merge that would change an authenticated
  principal's grant surface is blocked pending explicit admin confirmation or re-auth (AC20).
- **`fact_provenance` + `quarantined` (graft from C):** no provenance row, no readable fact;
  provenance shrink flips the fact to `quarantined` — fail-closed, reviewable, re-derivable.
  Schema and invariants land now so the Learn layer (Step 7) *cannot* ship facts un-anchored.
- **RLS policy corrected during review:** the grant-match must discriminate on `object_type`
  **alongside** `object_id IN (id, container_id)` — without it, an `email_message` UUID
  colliding semantics with a Slack container id would cross-match. Sketch:
  `visibility_scope = 'org' OR EXISTS (SELECT 1 FROM acl_grant g WHERE g.org_id = t.org_id
  AND g.person_id = current_setting('app.current_person_id', true)::uuid AND g.revoked_at IS
  NULL AND ((g.object_type = '<row-type>' AND g.object_id = t.id) OR (g.object_type =
  '<container-type>' AND g.object_id = t.container_id)))`.
- **Connector lifecycle contradiction resolved:** policy's connection predicate hides rows
  only on `disconnected`. **Pause keeps AI access live; disconnect revokes at next query** —
  matching the ingestion design's pause/disconnect/delete semantics, enforced in the
  predicate so no retrieval path can forget it.
- **Email default is recipient-rich, not private-by-default:** grants from `email_recipient`
  + connection owner. This is what makes MVP memory rich. Granularity: **per-message** for
  email, **container** for Slack/files (channel/folder), keeping `acl_grant` row counts sane.
- **Owner-copy rule (corrected in review):** `email_message` is one row per connected mailbox
  — the **owner's copy**, with full `headers` JSONB and, on Sent copies, the BCC set. A grant
  to a non-owner recipient (especially one whose mailbox is not connected, making the
  sender's copy the only copy) must not serve content the recipient could not see at the
  source: at the source, their inbox copy has no BCC line. Non-owner grants therefore resolve
  to a **redacted projection of the recipient's source-view** — BCC recipients, the full
  header chain, and owner-only folder/annotation context are stripped (AC19). The recipient's
  own connected copy, where it exists, is the authoritative full view. CA-CONN-05's
  full-headers retention review is wired in as a precondition.
- **UNKNOWN ⇒ DENY is a named rule** in `grant_writer.py`, not an emergent property: any
  unmappable principal, unverified identity, or ambiguous audience writes **no grant**.
  Over-restriction is then healed by an **end-user action** (one-click promotion queue, v1
  review-queue pattern — same UX muscle as entity-resolution review), not an admin ticket.
- **Live re-check is bounded (graft from B, narrowed):** only restricted Slack
  private-channel/DM hits; cached `conversations.members`, TTL 60–300 s, resource-group
  granularity. The DB grant is the **floor**; the live check may only **narrow**, never
  widen; **transient** check failure ⇒ floor stands — **bounded by a hard staleness TTL**
  (corrected in review): the ingest rule is UNKNOWN ⇒ DENY but a permissive "floor stands
  forever" during an API outage would invert it at re-check time, so a restricted Slack grant
  whose membership snapshot cannot be refreshed within the TTL is **suspended fail-closed**
  until a fresh snapshot lands (AC7). No source API ever sits on the general query path.
- **Deletion fidelity:** source object gone (sync or live check) ⇒ tombstone + enqueue
  chunk/content purge via the existing work-queue. Deny-but-retain rejected.
- **Shrink-without-deletion propagates (corrected in review):** revocation is not only
  deletion. When access is removed but the object survives (folder un-shared, membership
  narrowed, future Drive/SharePoint container shrink), **no 404 ever fires** — so grant
  writing is a **reconciliation, not an append**: each sync diffs prior vs current
  recipient/container membership and tombstones removed principals' grants
  (**SHRINK ⇒ TOMBSTONE**, a named rule alongside UNKNOWN ⇒ DENY; AC21). Without this, "if I
  revoke access, does the AI stop surfacing it?" is answered "only on deletion" — not
  acceptable to a works council.
- **Erasure scrub-vs-retain (per table, explicit):** `acl_grant` — **delete** with tenant
  (pure access metadata); `principal_source_identity` — **delete** (external identifiers are
  PII); `visibility_promotion` — **delete the mutable rows**; the lineage survives in the
  **retained** append-only `audit_log` (Art. 17(3) basis, same honesty model as PC-06);
  `fact_provenance` — **delete** with the facts it anchors. All via the erasure-hook
  registry; `identity` imports nothing from `access`.
- **Decision telemetry doubles as the staleness alert** Design A's §5 promised: allow/deny
  counts + resource keys (never content) in `audit_log`; starved retrieval triggers a
  reduced-coverage disclosure in the answer instead of silently confident half-answers.
- **Engine seam fail-closed by construction (corrected in review):** the whole invariant
  ("the role cannot widen") is a property of the `oneai_app` pool — a tenant flow that
  mistakenly acquires the BYPASSRLS `global_engine` bypasses org AND person RLS, silently,
  across every org. Developer convention is not a control against a silent, maximally
  permissive failure mode. So: the `access`/retrieval package has **no import path to
  `global_engine`** (module-import guard); `scoped_session` **aborts unless
  `current_user = 'oneai_app'`** (runtime assertion); both proven structurally by AC18, which
  is a FIX_BEFORE_PROD blocker until green.
- **Chunk→grant contract specified now (corrected in review):** how a future `chunk` (or
  `fact`) row binds to `acl_grant` is the load-bearing join of the whole design and cannot be
  deferred to the epic PF-01 gates. Contract: a chunk MUST carry its **parent** content row's
  `object_type`/`object_id`/`container_id` (the visibility policy joins on the parent's
  identifiers, never the chunk's own UUID) and inherit `visibility_scope`/`origin_scope` from
  the parent **in the same transaction** that creates it. Wrong-object-id wiring fails closed
  but silently hides the owner's own data; wrong-scope defaulting fails open — both excluded
  by contract + AC22's principal-set-equivalence invariant (failing placeholder until the
  tables exist, AC9 pattern). Same contract binds facts to `fact_provenance`.
- **Failure-closed invariants enumerated explicitly** (the spec's contract, each backed by a
  test in §4): (1) person GUC unset → org-scope rows only; (2) unmappable/unverified
  principal → no grant; (3) disconnected connector → rows hidden (pause ≠ disconnect);
  (4) content table without visibility policy → CI FAIL; (5) fact without provenance →
  unreadable, provenance shrink → quarantined; (6) live re-check narrow-only, UNKNOWN ⇒ DB
  floor stands (bounded by the staleness TTL — beyond it, suspend fail-closed); (7) widening
  without a `visibility_promotion` row → impossible by construction, on INSERT and UPDATE
  alike; (8) tenant/retrieval flow on the global engine → refused by construction (import
  guard + `current_user` assertion); (9) source ACL shrink without deletion → tombstoned at
  next sync (SHRINK ⇒ TOMBSTONE); (10) merged/ambiguous authenticated principal → zero
  restricted grants; (11) chunk/fact principal set ≠ parent/provenance principal set →
  invariant FAIL.

## 7. Remaining + notes

**FIX_BEFORE_PROD — new entries (append-only register; closure = linked PR + live-environment evidence, a green unit test is not closure):**

- [ ] **PF-FBP-1 — Sync-staleness alerting (15-min window) — PULLED FORWARD (review):**
  alert when a connector's ACL snapshot is older than the sync SLA. Sync reconciliation
  (SHRINK ⇒ TOMBSTONE, AC21) now ships **inside PF-01** as the shrink-propagation mechanism
  for all sources, and the Slack staleness TTL (AC7) suspends grants fail-closed — but
  staleness **alerting** is the only signal that reconciliation itself has stopped running
  for non-Slack sources, so it lands with PR-PF-1's telemetry rail, not later.
  *Where:* `access/services/decision_telemetry.py` + sync runner.
- [ ] **PF-FBP-2 — History-replay re-check:** multi-turn replay must re-filter revoked/
  quarantined content before context assembly (B's §8.3 leak). The invariant test ships now;
  the enforcement lands with the chat layer. **No chat layer goes live without it.**
- [ ] **PF-FBP-3 — Nightly Board system principal, break-glass treatment before Step 7:**
  dedicated role, mandatory logged-session seam (the `grant_is_active()` pattern), never a
  silent BYPASSRLS-equivalent. *Why deferred:* the Nightly Board does not exist yet; the
  entry exists so it cannot be built casually.
- [ ] **PF-FBP-4 — Audience-intersection vs Learn-layer tension:** a fact distilled from
  restricted sources needs an audience rule (intersection? promotion-only?). Gets its **own
  design pass** before Step 7 writes the first fact. `fact_provenance` + `quarantined` is
  the fail-closed floor until then.
- [ ] **PF-FBP-5 — Deferred deliberately, back pocket:** B's fourth-role column-privilege
  trick — only if a non-RLS retrieval path ever appears. Not scheduled.

**Sequencing note:** this epic is the gate for EPIC "Ask". `chunk`, embeddings, BM25, and the
agent loop are built **only after** 0019 + the visibility policies + the invariant tests —
**including AC18's engine-seam guard (import guard + runtime role assertion)** — are
green on `oneai_app`, and only **under the chunk→grant contract (§6 / AC22)** — the retrieval
layer must never exist un-scoped, even on a branch.

**Dynamic adversarial QA pass (after PR-PF-1):** live attempts to (a) read another person's
restricted email via raw SQL on the `oneai_app` engine, (b) widen scope by UPDATE without a
promotion row, (c) resurrect revoked content via history replay, (d) exploit GUC-unset and
forged-person-id paths, (e) verify pause-vs-disconnect at the wire, (f) acquire
`global_engine` from the access/retrieval path and read restricted rows (must be refused),
(g) INSERT a restricted-origin row directly as `visibility_scope='org'` without lineage,
(h) retrieve a sender's Sent copy as a non-owner To-recipient and check for BCC/header
leakage. Findings → audit doc in
`docs/audits/`, same discipline as the erasure dynamic pass (TC-ER-0xx).

**Verticals note:** the same `acl_grant`/`visibility_promotion` spine is what New-I (ISUN
case files) and Vetera (patient records) will lean on — permission fidelity is Tier 1
platform, never re-implemented per vertical.

---

## Cross-vendor review addendum (GPT-5.5 xhigh, 2026-06-09) — design CONFIRMED, four amendments

An independent GPT-family review confirmed Design A as the right base ("pure B is operationally
brittle and still not perfectly provable; pure C is product-hostile") and contributed four
amendments that are **part of this spec**:

1. **Reframe the invariant for provability.** "Never surfaces" becomes a *provable authorization
   model with explicit freshness bounds*: every surfaced fact carries source provenance, audience
   derivation, ACL freshness (age of the backing grant snapshot), and an auditable fail-closed
   path. A **published per-scope-class freshness SLA** is the works-council artifact — not a
   metaphysical "always live" claim. (Consistent with the §-level UNKNOWN⇒DENY + TTL rules; this
   names the contract.)
2. **Widen the risk-tiered live-narrowing set.** The targeted live re-check (DB grant = floor,
   narrow-only) applies not just to Slack private channels/DMs but to every *high-risk mutable
   scope*: **shared mailboxes, external file shares, distribution-list-derived email grants, and
   departed/terminated users**. Departure and 403/404 events invalidate access *immediately*
   (priority queue), ahead of any bulk reconciliation.
3. **Identity is the real attack surface — harden audience derivation.** (a) Group/list/shared
   identity grants are **non-personal until expanded from verified, timestamped membership
   snapshots**; unverifiable ⇒ restricted/no-grant, never guessed. (b) Each connector must declare
   its **retroactivity semantics** explicitly (Slack: joining a channel exposes history ⇒ container
   grant correct; sources where access is non-retroactive get time-bounded grants). (c) Email
   "recipient = allowed" is a partial rule — forwarding, aliases, delegates, and migrations stay
   conservative (no grant) until modeled.
4. **PF-FBP-4 is resolved in principle — the `audience_expression` rule for derived facts.** A
   derived fact is visible only to principals who could see *enough provenance to justify that
   exact fact*, unless explicitly promoted or safely generalized: single-source facts inherit the
   source audience; conjunctive multi-source facts take the **intersection** (each source
   necessary); corroborated-duplicate facts may take the **union** (each source independently
   sufficient); aggregate/statistical facts require **k-anonymity / minimum-source thresholds** +
   removal of source-identifying detail; anything ambiguous stays `quarantined`. Org memory grows
   via promotion and safe aggregation — never via laundering. (PF-FBP-4's dedicated design pass
   now starts from this rule rather than from open questions.)

**Scale forecast (plan for it, don't pre-build):** the first thing to break is the **RLS ×
pgvector interplay** (ANN wants neighbors before authorization; RLS filters before disclosure),
not `acl_grant` row volume. Mitigations in order: two-stage retrieval (over-fetch candidates
inside RLS, ACL-aware re-rank), org-scoped vector indexes, grant-set hashing, and — only if
measured necessary — a materialized `accessible_object(person_id, object)` index maintained by
the reconciliation job.
