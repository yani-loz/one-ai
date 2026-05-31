# TC-IA-066: Field-bounds fuzz on `POST /users` (empty/oversize/unicode/null-byte)

| Field | Value |
|---|---|
| **ID** | TC-IA-066 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Input validation on auth surfaces (IV) |
| **Type** | Fuzz |
| **Severity if it fails** | Low |
| **Status** | Executed |
| **Result** | ❌ Fail → ✅ **FIXED** (2026-05-31) |
| **Finding tag** | NEW |

## Objective
Fuzz the `full_name` / `role` field bounds on `POST /users` and assert **no `500`** on
any variant: `full_name=""` → `422`; `full_name` length 201 → `422`; `full_name` with a
NUL byte and with RTL/emoji → report behavior; missing required field → `422`; wrong
JSON type (`role` as a number) → `422`.

## Break hypothesis
The well-bounded cases (`min_length`/`max_length`/missing/wrong-type) should all be
clean `422`s. The interesting probe is an input that PASSES Pydantic but the DB rejects:
an embedded NUL byte (`\x00`) is a valid Python/JSON string but invalid in PostgreSQL
UTF-8 text — if it reaches the INSERT, asyncpg raises and (if unmapped) surfaces as a
`500`, mirroring the AUD-02 class of "valid-per-schema input → opaque 500" but on a
different field.

## Preconditions
- Live stack; namespace `iv-066-*`; fresh org + admin.

## Steps
1. Onboard fresh org + admin; company-login.
2. `POST /users` with: `full_name=""`; `full_name="X"*201`; `full_name="Ab\x00cd"`
   (NUL byte); `full_name="user‮evil😈"` (RTL override + emoji); body with `role`
   omitted; body with `role=12345` (number).
3. Record status + body for each; assert no `500`.

## Expected result (contract)
`422` for empty, oversize, missing-field, and wrong-type. NUL-byte and RTL/emoji either
`422` (rejected) or `201` (stored literally) — but **never `500`**.

## Harness
Script: `harness/tc_066.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_066.py`

---

## Execution result

- **Run at:** 2026-05-31 12:04 local
- **Result:** ❌ Fail (a win — NEW defect reproduced)
- **Finding tag:** NEW

**Actual behavior**

> Five of six variants behaved per contract (`422` for empty / oversize / missing-role /
> role-as-number; `201` storing the RTL+emoji name verbatim). The **NUL-byte `full_name`
> produced an HTTP `500 "Internal Server Error"`** — the "no 500s" assertion FAILED. The
> NUL byte passes Pydantic (`Field(min_length=1, max_length=200)` counts it as a char)
> but PostgreSQL rejects `0x00` in UTF-8 text at INSERT, and that DB error is unmapped.

**Evidence** (harness)

```
[onboard] status=201
[full_name=''] status=422 body={"detail":[{"type":"string_too_short","loc":["body","full_name"],"msg":"String should have at least 1 character","input":"","ctx":{"min_length":1}}]}
[full_name len=201] status=422 body={"detail":[{"type":"string_too_long","loc":["body","full_name"],"msg":"String should have at most 200 characters",...}
[full_name NUL byte] sent_bytes=4162006364 status=500 body=Internal Server Error
[full_name RTL+emoji] sent='user‮evil😈' status=201 stored='user‮evil😈' exact=True
[missing role] status=422 body={"detail":[{"type":"missing","loc":["body","role"],"msg":"Field required",...}
[role=number] status=422 body={"detail":[{"type":"enum","loc":["body","role"],"msg":"Input should be 'company_admin' or 'member'","input":12345,...}
```

**Evidence** (backend traceback — root cause)

```
  File "/app/app/identity/routes/user_routes.py", line 50, in create_user
  File "/app/app/identity/services/user_service.py", line 74, in create_user
  File "/app/app/identity/repositories/user_repository.py", line 104, in add
  ...
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error)
  <class 'asyncpg.exceptions.CharacterNotInRepertoireError'>:
  invalid byte sequence for encoding "UTF8": 0x00
```

`psql` after the 500 confirms the `users` table is intact (158 rows) — no corruption,
the INSERT simply aborted.

**Verdict**

Defense **broke** — **NEW**, severity **Low** (per the suite's stated severity for this
case; matches AUD-02's "self-inflicted correctness/availability on valid-per-contract
input" class — authenticated, no data exposure).

- **Code path.** `UserCreateRequest.full_name` (`user_schemas.py:48`) bounds character
  count only, so `"Ab\x00cd"` (5 chars) passes validation. The value flows to
  `UserService.create_user` → `UserRepository.add` → `session.flush`
  (`user_repository.py:101-105`), where asyncpg raises
  `CharacterNotInRepertoireError` (a `DBAPIError`). That is neither an `IntegrityError`
  (so `create_user`'s `except IntegrityError` at `user_service.py:75` does not catch it)
  nor an `IdentityError` (so `error_handlers.py` has no mapping) → it bubbles to
  FastAPI's default handler → **HTTP 500**.
- **Relationship to AUD-02.** Same *shape* as AUD-02 (schema-valid input → opaque 500
  from an unguarded lower layer) but a **different field and trigger** (NUL byte in
  `full_name` at the DB, vs. >72-byte password at bcrypt). AUD-02 is the password path
  and is fixed; this `full_name` NUL-byte path is not covered by AUD-02 or any
  `FIX_BEFORE_PROD.md` item — confirmed by grep. Hence **NEW**, not CONFIRMS/REFUTES.

**Notes / follow-up**

Remediation options: reject control characters / `\x00` at the schema boundary (a
`field_validator` on `full_name`, applied to all free-text fields including `org_name`
and `admin_full_name`), or add a global handler mapping `DBAPIError` /
`CharacterNotInRepertoireError` to a clean `400/422`. The same NUL-byte trigger likely
affects `POST /platform/orgs` (`org_name`, `admin_full_name`) — worth a follow-up case in
the `IV` suite.

---

## Remediation (2026-05-31) — ✅ FIXED

Both remediation options were applied: control characters (NUL / C0 / DEL) are **rejected at the validation boundary** (`SafeName` → 422) on all free-text fields (`full_name`, `org_name`, `admin_full_name`), **and** a global `DataError` handler maps any bad-data DB error that slips past the validators to 422 instead of an opaque 500 (defense in depth — covers the `/platform/orgs` fields the case flagged).

- **Code:** `SafeName` in `backend/app/identity/schemas/user_schemas.py`; `_handle_data_error` registered in `backend/app/main.py`.
- **Re-verified live (this harness):** NUL-byte `full_name` → `422` ("must not contain control characters"), was `500`.
- **Regression test:** `test_user_routes.py::test_create_user_nul_byte_in_full_name_returns_422`.
- **Tracked:** DYN-03.
