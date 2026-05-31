from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# TC-IA-004 — RLS is DEFINED BUT INERT (superuser bypass) (Adversarial,
#             CONFIRMS-DOCUMENTED). Sev High (documented single-point-of-failure).
#
# This case is DB-ground-truth, not an HTTP probe: the app connects to Postgres as
# role `oneai`, which is SUPERUSER + BYPASSRLS + the table owner — all of which
# unconditionally bypass Row-Level Security. So the `org_isolation` policy defined
# in migration 0003 (USING/WITH CHECK org_id = current_setting('app.current_org_id')
# ::uuid) never filters. The ONLY active tenant control is the app-layer org_id
# filter in the repositories.
#
# The driver is psql against the `db` container (the backend container has no psql
# client). Because `import jwt` / httpx in the inlined _common would add no value
# here, this file documents the EXACT psql commands that produce the evidence; run
# them from the repo root. SET is session-scoped, so the bogus GUC and the count
# MUST share one -c string (one connection).
#
# RUN (repo root):
#   docker compose exec -T db psql -U oneai -d oneai \
#     -c "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='oneai';"
#   docker compose exec -T db psql -U oneai -d oneai \
#     -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='users';"
#   docker compose exec -T db psql -U oneai -d oneai \
#     -c "SET app.current_org_id='00000000-0000-0000-0000-000000000000'; \
#         SELECT current_setting('app.current_org_id', true) AS guc, \
#                count(*) AS users_visible FROM users;"
#
# BREAK HYPOTHESIS / PROOF: a bogus all-zeros org GUC matches NO real org, so a
# RLS-enforcing engine returns 0 rows. If count > 0 (it returns hundreds across many
# distinct orgs), RLS is proven inert — the documented single point of failure.
#
# No Python logic to run; the SQL above IS the harness. Printing the plan so a stdin
# run of this file is self-describing.
# ─────────────────────────────────────────────────────────────────────────────

PLAN = """\
TC-IA-004 is executed via psql (db container), not httpx. Commands:

1) Role privileges:
   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='oneai';
   Expect: rolsuper=t, rolbypassrls=t  -> both bypass RLS unconditionally.

2) RLS enabled on the table:
   SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='users';
   Expect: relrowsecurity=t (policy is ENABLED) but relforcerowsecurity=f (not forced).

3) Bogus GUC + count (ONE session / one -c):
   SET app.current_org_id='00000000-0000-0000-0000-000000000000';
   SELECT count(*) FROM users;
   Expect under enforcement: 0. Actual: hundreds (RLS bypassed by superuser/owner).
"""

print(PLAN)
