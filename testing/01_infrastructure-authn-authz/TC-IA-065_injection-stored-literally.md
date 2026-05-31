# TC-IA-065: SQL/script injection in name fields stored literally (no SQLi)

| Field | Value |
|---|---|
| **ID** | TC-IA-065 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Adversarial |
| **Severity if it fails** | High |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Prove the write paths are SQL-injection-safe (parameterized) and store untrusted name
content literally: a `full_name` of `Robert'); DROP TABLE users;--` and of
`<script>alert(1)</script>`, plus an org `org_name` carrying the SQLi payload, must
create successfully, leave the `users` table intact, and round-trip BYTE-FOR-BYTE in
`GET /users`. (XSS-on-render is a frontend concern, out of scope here.)

## Break hypothesis
If any query interpolated these values into SQL text instead of binding them as
parameters, the `DROP TABLE users;--` payload would execute (table dropped → subsequent
reads 500, `to_regclass('users')` NULL). Any sign of SQL execution = **High** (CWE-89).

## Preconditions
- Live stack; namespace `iv-065-*`; fresh org (org_name = SQLi payload) + admin.
- DB ground-truth via `psql -U oneai -d oneai`.

## Steps
1. Onboard a fresh org with `org_name = "Robert'); DROP TABLE users;--"`.
2. Company-login; `POST /users` with `full_name` = the SQLi payload, then the XSS payload.
3. `GET /users`; compare stored `full_name` to sent, byte-for-byte (hex).
4. `POST /users` once more (probe the table still accepts inserts).
5. `psql`: `SELECT to_regclass('public.users')`, row count, and the stored payloads.

## Expected result
All creates `201`; payloads stored verbatim (hex equal); `users` table still exists;
follow-up insert `201`. No SQL executed.

## Harness
Script: `harness/tc_065.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_065.py`

---

## Execution result

- **Run at:** 2026-05-31 12:03 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Both name payloads and the org_name payload were accepted (`201`) and stored
> byte-for-byte (hex of sent == hex of stored). The `users` table survived (`to_regclass`
> = `users`, 158 rows), a post-injection insert succeeded (`201`), and `psql` shows the
> literal payloads in the table — no SQL executed.

**Evidence** (harness)

```
[onboard org_name=SQLI] status=201
[org_name round-trip] sent="Robert'); DROP TABLE users;--" stored="Robert'); DROP TABLE users;--" exact=True
[create sqli] status=201 sent="Robert'); DROP TABLE users;--" stored="Robert'); DROP TABLE users;--" exact=True
[create xss] status=201 sent='<script>alert(1)</script>' stored='<script>alert(1)</script>' exact=True
[GET /users iv-065-sqli-...@example.com] stored="Robert'); DROP TABLE users;--" byte_exact=True bytes_sent=526f6265727427293b2044524f50205441424c452075736572733b2d2d bytes_stored=526f6265727427293b2044524f50205441424c452075736572733b2d2d
[GET /users iv-065-xss-...@example.com] stored='<script>alert(1)</script>' byte_exact=True bytes_sent=3c7363726970743e616c6572742831293c2f7363726970743e bytes_stored=3c7363726970743e616c6572742831293c2f7363726970743e
[users table alive? post-injection create] status=201
[list after injection] status=200 user_count=3
```

**Evidence** (DB ground-truth — `psql -U oneai -d oneai`)

```
 users_table | user_rows 
-------------+-----------
 users       |       158

           full_name           
-------------------------------
 Robert'); DROP TABLE users;--
 <script>alert(1)</script>
```

**Verdict**

Defense **held**. All identity writes go through SQLAlchemy Core/ORM with bound
parameters (`UserRepository.add` → `session.flush`, `OrganizationRepository.add`); no
query interpolates user input into SQL text. The `DROP TABLE` payload was stored as data,
not executed — `to_regclass('public.users')` is non-null and the table holds 158 rows.
Round-trip is byte-exact (matching hex), so the API neither escapes nor mangles the
value. No SQLi (CWE-89). XSS-on-render is explicitly out of scope (a frontend/`FE`
target concern). Passing adversarial control.

**Notes / follow-up**

Output-encoding for the stored `<script>` payload is the React frontend's responsibility
(planned `FE` target); the backend correctly stores it literally.
