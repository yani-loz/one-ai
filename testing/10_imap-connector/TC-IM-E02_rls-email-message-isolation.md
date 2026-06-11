# TC-IM-E02 — Live RLS isolation on the email Layer-1 tables

| ID · Suite · Type · Mode |
|---|
| TC-IM-E02 · E (Persistence/RLS/entity graph) · Adversarial · db-rls |

| Result · Tag · Severity · Status |
|---|
| ✅ Pass · ✔ CONFIRMS-FIXED · — · Executed |

## Objective
Prove that PostgreSQL Row-Level Security actually bites on the densest-PII email tables
(`email_message` / `email_recipient` / `email_attachment`) for the real runtime tenant role
`oneai_app` (NOSUPERUSER, **NOBYPASSRLS**) — not just on `person`/`person_email` (E01). RLS on these
tables got its `org_isolation` policy in migration `0008` and `FORCE ROW LEVEL SECURITY` in `0009`.

## Break hypothesis
Every functional ingest test runs on the BYPASSRLS `oneai_global` engine, so DB-level isolation on
the email Layer-1 tables is catalog-proven, not row-proven. A cross-tenant `SELECT` as `oneai_app`
might leak another org's email, or a cross-org `INSERT`/`UPDATE` might slip the policy `WITH CHECK`.

## Steps
1. As the OWNER engine, seed two run-stamped throwaway orgs (uuid4 each), one `connector_connection`
   + one `email_message` (+ one `email_recipient` + one `email_attachment`) per org.
2. As `oneai_app` with `app.current_org_id`=A, then =B, SELECT the tagged `email_message` rows.
3. As `oneai_global` (BYPASSRLS), SELECT the same — the teeth: rows must exist for BOTH orgs.
4. Per-org child isolation: global sees both orgs' recipient/attachment rows; A-scoped app sees only A's.
5. As `oneai_app` scoped to A, attempt a cross-org `INSERT` (org B) and a cross-org `UPDATE` (move A→B).
6. Cleanup: delete the run-stamped connections (email + children CASCADE).

## Expected
A-scoped app sees only A; B-scoped sees only B; global sees both; cross-org INSERT and UPDATE are
rejected with `new row violates row-level security policy`. (Expected per catalog: ✅ ✔.)

## Execution result (2026-06-09)
Harness: `testing/10_imap-connector/harness/rls_email_message_isolation.py`
Command: `docker compose exec -T backend python - < testing/10_imap-connector/harness/rls_email_message_isolation.py`

```
seeded ORG_A=f94e7f9f-bbae-4501-950c-3b0ceb2d9660 ORG_B=bed1c280-8d96-4677-9613-20ab81d60c7f (tag rls-e02-a9c2c65b8d1d)
  [PASS] app_scoped_A_sees_only_A :: visible={'f94e7f9f-...-3b0ceb2d9660'}
  [PASS] app_scoped_B_sees_only_B :: visible={'bed1c280-...-20ab81d60c7f'}
  [PASS] global_bypassrls_sees_both :: visible={'f94e7f9f-...', 'bed1c280-...'}
  [PASS] recipient_per_org_isolation :: global sees 2 (both orgs), A-scoped app sees 1 (only A)
  [PASS] attachment_per_org_isolation :: global sees 2 (both orgs), A-scoped app sees 1 (only A)
  [PASS] app_cross_org_message_insert_rejected :: InsufficientPrivilegeError: new row violates row-level security policy for table "email_message"
  [PASS] app_cross_org_message_update_rejected :: InsufficientPrivilegeError: new row violates row-level security policy for table "email_message"
cleanup: deleted 2 connector_connection rows (+cascade email/children)

RESULT: 7/7 checks passed
VERDICT: RLS HOLDS on the email Layer-1 tables
```

**Verdict:** ✅ **Pass** — RLS holds live on all three email Layer-1 tables. The teeth fire (global
sees both orgs' rows, so the test is not vacuously green) and the `WITH CHECK` rejects both a
cross-org INSERT and a cross-org UPDATE. Cleanup deleted 2 connections (+cascade); zero orphans.
**Tag:** ✔ CONFIRMS-FIXED — confirms the migration `0009` RLS flip on the email surface.
