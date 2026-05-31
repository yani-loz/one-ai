<!--
  TC-IA-012 — unknown email must be byte-identical to wrong-password (no enumeration).
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-012: Unknown email returns the SAME 401 as a wrong password (no user enumeration)

| Field | Value |
|---|---|
| **ID** | TC-IA-012 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Adversarial |
| **Severity if it fails** | Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
Prove `/auth/login` does not leak account existence: a never-registered email must yield
the SAME status code AND the SAME body (byte-for-byte) as a real email with a wrong
password (TC-IA-011). Any difference is a user-enumeration oracle.

## Break hypothesis
Attacker bet: an unknown email returns a different status (e.g. 404), a different message
("user not found"), a different body shape, or differs in headers/length — letting an
attacker harvest which emails are registered across all tenants.

## Preconditions
Live stack. Harness onboards a fresh org `authn012-<stamp>` and captures TWO responses
in the SAME process: (A) the real admin email + wrong password, (B) a never-registered
`ghost-authn012-<stamp>@oneai.dev` + any password. Then it compares status, raw body
bytes, and the `detail` string. The audit (AUD §5, constant-time login "✅ Holds") claims
the dummy-hash path makes these indistinguishable — this case verifies that empirically.

## Steps
1. Onboard fresh org + admin.
2. Response A: POST `/auth/login` {real_email, "WRONG-Pass-9999!"}.
3. Response B: POST `/auth/login` {ghost_email, "WRONG-Pass-9999!"}.
4. Compare A.status==B.status, A.content (raw bytes) == B.content, A.json==B.json.

## Expected result
- Both `401`.
- `A.content == B.content` (identical raw bytes).
- Both bodies == `{"detail": "Invalid email or password."}`.

## Harness
Script: `harness/tc_012.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_012.py`

---

## Execution result

- **Run at:** 2026-05-31 11:48 local
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior**

> The wrong-password response (real email) and the unknown-email response are byte-for-byte
> identical: same 401 status, same raw body bytes, same `detail`. No enumeration oracle on
> the response surface.

**Evidence**

```
== onboard == 201 real_admin=admin-authn012-19e7d3277301333@oneai.dev
== A: real email + wrong password ==
  status: 401
  raw bytes: b'{"detail":"Invalid email or password."}'
== B: ghost (never-registered) email + wrong password ==
  status: 401
  raw bytes: b'{"detail":"Invalid email or password."}'
status equal (A==B): True
raw body bytes equal (A==B): True
json equal (A==B): True
RESULT: PASS
```

**Verdict**

Defense held — CONFIRMS-FIXED of the audit's "Constant-time login ✅ Holds" claim on the
response-content axis. `auth_service.login` (`auth_service.py:61-68`) verifies against
`DUMMY_PASSWORD_HASH` (`security/password.py:60`) when no user matched, then funnels both
the unknown-email and wrong-password branches through one
`InvalidCredentialsError("Invalid email or password.")`. The bodies are indistinguishable;
no response-side enumeration. (Timing-channel measured separately in TC-IA-016.)

**Notes / follow-up**

This is the response-content proof. The complementary timing-channel sanity is TC-IA-016.
