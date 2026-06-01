<!--
  Test-case: TC-BG-035. Authored before running; Execution result block written back after.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-BG-035: SQL-injection reason stored literally — parameterized, content-blind, table intact

| Field | Value |
|---|---|
| **ID** | TC-BG-035 |
| **Target** | Break-glass support access (PC-05) |
| **Suite** | AEA — Audience confinement + live expiry + audit + input |
| **Type** | Adversarial / Fuzz |
| **Severity if it fails** | Critical |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
A classic SQL-injection payload in `reason` must be stored as a literal string (parameterized
queries), not executed. The returned `reason` equals the input byte-for-byte, the `support_grant`
table survives (no DROP), and the response exposes only metadata (`SupportGrantResponse`).

## Break hypothesis
If `reason` were ever concatenated into raw SQL (it should not be — SQLAlchemy ORM parameterizes),
the payload `Robert'); DROP TABLE support_grant;--` would drop the table or corrupt the insert.
The bet: the table disappears, the insert errors, or the stored value is mangled.

## Preconditions
- Live stack `:8000`. Real platform token. Fresh run-stamped org `aea35-<stamp>`.
- The payload `Robert'); DROP TABLE support_grant;--` (the canonical little-Bobby-Tables string).
- psql ground-truth on the **db** container to prove (a) the table still exists and (b) the stored
  `reason` is the exact literal.

## Steps
1. Provision org A. `POST /platform/orgs/{A}/support-requests` with `reason` = the injection
   payload.
2. Assert 201 and that the returned `reason` equals the input exactly.
3. psql: `SELECT to_regclass('public.support_grant')` (table intact) and
   `SELECT reason FROM support_grant WHERE id='<grant>'` (stored value == payload).
4. Confirm the response body contains only `SupportGrantResponse` metadata keys (no extra fields,
   no content leakage).

## Expected result
- 201; returned `reason` == the payload literally.
- `support_grant` table still exists; another fresh request still succeeds afterward (table
  usable).
- Stored `reason` in the DB equals the payload byte-for-byte.
- Response keys are exactly the metadata set (id, org_id, requested_by_admin_id,
  requested_by_email, reason, status, is_active, decided_at, decided_by_email, expires_at,
  created_at) — content-blind.

## Harness
Script: `harness/tc_035.py` (API + payload round-trip; psql verifies table + stored literal) · run: `cat testing/07_break-glass/harness/_common.py testing/07_break-glass/harness/tc_035.py | docker compose exec -T backend python -`

---

## Execution result

- **Run at:** 2026-06-01 22:05 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The injection payload was accepted (201) and stored verbatim. The returned `reason` equalled the
> input exactly. psql confirmed the `support_grant` table still exists and the stored `reason` is
> the literal payload. A subsequent request on the table still succeeded — the DROP was never
> executed. The response carried only the metadata keys.

**Evidence**

```
1) provisioned A=731ef95b-1730-4feb-b179-affe4210bda4
2) POST request reason="Robert'); DROP TABLE support_grant;--": 201
   returned reason == input?  -> True
3) response keys: ['created_at','decided_at','decided_by_email','expires_at','id','is_active',
                   'org_id','reason','requested_by_admin_id','requested_by_email','status']
   == SupportGrantResponse metadata set -> True (no content leakage)
4) follow-up request on same table: 201 (table usable)
   GRANT_ID=8a68ba26-6e67-4ef8-a750-390a1b9897a8

-- psql on db container:
$ docker compose exec -T db psql -U oneai -d oneai -c "SELECT to_regclass('public.support_grant');"
  to_regclass
---------------
 support_grant      <- table intact
$ ... -c "SELECT reason FROM support_grant WHERE id='8a68ba26-...';"
                reason
---------------------------------------
 Robert'); DROP TABLE support_grant;--   <- stored literally
PASS injection stored as literal; table intact; content-blind response
```

**Verdict**
The defense held. The insert goes through the SQLAlchemy ORM
(`SupportGrantRepository.insert`, support_grant_repository.py:37-41 → `session.add` + `flush`),
which parameterizes the value — the payload is bound, never interpolated into SQL, so the DROP is
inert text. The response is shaped by `SupportGrantResponse` (support_schemas.py:29-46), exposing
only metadata, so a malicious reason cannot become a content-exfiltration vector. Confirms the
input-handling posture (content-blindness + parameterization) live.

**Notes / follow-up**
`reason` is the only free-text field and it is round-tripped to the approving company_admin
(informed consent) — so it is displayed in the company UI. The stored-literal property means the
frontend MUST render it as text (React escapes by default); that is an FE/XSS concern tracked under
the frontend target, not a backend defect. Pairs with TC-BG-034 (bounds).
