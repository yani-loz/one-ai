# TC-IA-004: RLS is defined but inert (superuser bypass)

| Field | Value |
|---|---|
| **ID** | TC-IA-004 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Infrastructure |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ⚠️ Pass-with-concern |
| **Finding tag** | CONFIRMS-DOCUMENTED |

## Objective
Empirically prove the documented reality: the `org_isolation` Row-Level Security policy
(migration `0003`) is **defined** on `users` but **inert**, because the app connects to
Postgres as `oneai` — a SUPERUSER + BYPASSRLS + table-owner role — which bypasses RLS
unconditionally. Therefore the app-layer `org_id` filter in the repositories is the **only**
active tenant control (a documented single point of failure).

## Break hypothesis
If RLS were enforcing, setting a bogus all-zeros `app.current_org_id` GUC (an org that owns
no rows) and selecting from `users` would return **0** rows. The bet: rows are still
returned across many distinct orgs, proving the policy never filters under the superuser
connection — so a single missed `WHERE org_id` in the app would leak cross-company data
with no DB backstop.

## Preconditions
Live stack. DB ground-truth via psql in the `db` container (the backend container has no
psql client; the task blesses `docker compose exec -T db psql`). Read-only `SELECT`s — no
mutation of any org or account. `SET` is session-scoped, so the GUC and the count share one
`-c` (one connection).

## Steps
1. `SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='oneai';` → expect
   `rolsuper = t`, `rolbypassrls = t`.
2. `SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='users';`
   → expect `relrowsecurity = t` (policy enabled) and `relforcerowsecurity = f` (not forced).
3. In one session: `SET app.current_org_id='00000000-0000-0000-0000-000000000000';
   SELECT count(*) FROM users;` → under enforcement this is 0; observe it is not.

## Expected result (per the documented design)
RLS does **not** filter: despite the bogus GUC, `SELECT count(*) FROM users` returns a
non-zero count spanning many orgs — confirming the inert state. (A truly enforcing engine
would return 0 for the all-zeros org.)

## Harness
Driver: psql against the `db` container (record/plan in `harness/tc_004.py`).
Run (repo root):
```
docker compose exec -T db psql -U oneai -d oneai -c "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='oneai';"
docker compose exec -T db psql -U oneai -d oneai -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='users';"
docker compose exec -T db psql -U oneai -d oneai -c "SET app.current_org_id='00000000-0000-0000-0000-000000000000'; SELECT current_setting('app.current_org_id', true) AS guc, count(*) AS users_visible FROM users;"
```

---

## Execution result

- **Run at:** 2026-05-31 11:46 local
- **Result:** ⚠️ Pass-with-concern
- **Finding tag:** CONFIRMS-DOCUMENTED

**Actual behavior**

> The app role `oneai` is `rolsuper = t` and `rolbypassrls = t`. RLS *is* enabled on
> `users` (`relrowsecurity = t`) but not forced (`relforcerowsecurity = f`). With a bogus
> all-zeros `app.current_org_id`, `SELECT count(*) FROM users` returned **239** rows across
> **126 distinct orgs** — proving the policy never filters under the superuser connection.

**Evidence**

```
=== Q1: app role privileges ===
 rolname | rolsuper | rolbypassrls
---------+----------+--------------
 oneai   | t        | t
(1 row)

=== Q3: RLS enabled on users? ===
 relname | relrowsecurity | relforcerowsecurity
---------+----------------+---------------------
 users   | t              | f
(1 row)

=== Q2: bogus GUC + count (same session) ===
SET
                 guc                  | users_visible
--------------------------------------+---------------
 00000000-0000-0000-0000-000000000000 |           239
(1 row)

=== Q4: distinct org_ids actually present (separate session) ===
 distinct_orgs | total_users
---------------+-------------
           126 |         240
(1 row)
```

(The count differs by one between the GUC session and the later separate count — 239 vs
240 — because parallel agents are inserting users into the shared DB during the run; both
figures prove the same fact: the bogus GUC returns hundreds of cross-org rows.)

**Verdict**

Behaves **as documented** — RLS is inert by design, not a new defect. The policy is
correctly defined (`backend/app/db/migrations/versions/0003_define_rls_policies.py`:
`ENABLE ROW LEVEL SECURITY` + `USING/WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)`),
but the app connects as `oneai`, which is both SUPERUSER and BYPASSRLS and the table owner —
each of which bypasses RLS unconditionally, and `FORCE ROW LEVEL SECURITY` is off. So the
**app-layer `org_id` filter is the only active tenant control**, exactly as recorded in
`docs/FIX_BEFORE_PROD.md` ("Enforce the (already-defined) Postgres RLS policy", lines 59-60)
and the audit's Limitations note. Severity High is retained because this is the documented
single-point-of-failure: one missed `WHERE org_id` in the repositories would leak another
company's data with no DB backstop. This is precisely why every tenant-scoped repo method
gets a cross-tenant negative test (TC-IA-030..036).

**Notes / follow-up**

Remediation (tracked, not actioned) per FIX_BEFORE_PROD lines 59-60: connect as a dedicated
**non-superuser, non-owner** role, grant scoped DML, provide a bypass path for the
legitimately-global flows (login / refresh / `/auth/me` / onboarding INSERT), add
`FORCE ROW LEVEL SECURITY`, and re-verify live that an enforced role returns zero org-B rows.
