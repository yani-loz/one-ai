<!--
  TC-IA-015 — platform login: valid → 200 tokens (no user field); wrong pw → generic 401.
  See ../README.md for the result legend, finding tags, and severity scale.
-->

# TC-IA-015: Platform-admin login — valid returns tokens (no user field); wrong password returns generic 401

| Field | Value |
|---|---|
| **ID** | TC-IA-015 |
| **Target** | Infrastructure + AuthN/AuthZ |
| **Suite** | Authentication / login (AUTHN) |
| **Type** | Positive / Negative |
| **Severity if it fails** | Info / Medium |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | — |

## Objective
Verify the `/platform/login` contract: a valid platform admin gets HTTP 200 with
`access_token`, `refresh_token`, `token_type="bearer"` and NO `user` field (platform
domain has no user view, served with `response_model_exclude_none`). A wrong password
gets the generic 401.

## Break hypothesis
Violation = valid login missing a token or `token_type`; a `user` object leaking into the
platform response (the platform domain must not carry one); OR a wrong-password attempt
returning anything other than 401 / a generic body (e.g. a distinguishing message, or a
500). We use the demo platform admin for the VALID read-only login (no mutation) and never
change its credentials.

## Preconditions
Live stack. Uses the demo platform admin `super@ethera.ai` for a read-only valid login
(no state mutated), and the same email with a wrong password for the negative case. No
fresh org needed; nothing is mutated.

## Steps
1. POST `/platform/login` {super@ethera.ai, correct pw} → expect 200, tokens, no `user`.
2. POST `/platform/login` {super@ethera.ai, "WRONG-Pass-9999!"} → expect 401 generic.
3. POST `/platform/login` {ghost@oneai.dev, anything} → expect SAME 401 generic.

## Expected result
- Step 1 `200`; body keys ⊆ `{access_token, refresh_token, token_type}`; `token_type ==
  "bearer"`; `user` key ABSENT.
- Steps 2 & 3 `401` with body `{"detail": "Invalid email or password."}`.

## Harness
Script: `harness/tc_015.py` · run: `docker compose exec -T backend python - < testing/01_infrastructure-authn-authz/harness/tc_015.py`

---

## Execution result

- **Run at:** 2026-05-31 11:58 local
- **Result:** ✅ Pass
- **Finding tag:** —

**Actual behavior**

> Valid platform login returns 200 with both tokens and `token_type=bearer`; the `user`
> field is omitted entirely. Wrong-password (real platform email) and unknown platform
> email both return the same generic 401. The platform auth domain matches the company
> domain's anti-enumeration behavior and omits the user view.

**Evidence**

```
== A: valid platform login (super@ethera.ai) == 200
   body keys: ['access_token', 'refresh_token', 'token_type']
   token_type: bearer
   'user' key present: False
== B: platform login real-email WRONG password == 401
   body: {'detail': 'Invalid email or password.'}
== C: platform login GHOST email == 401
   body: {'detail': 'Invalid email or password.'}
B raw == C raw: True
RESULT: PASS
```

**Verdict**

Defense held. `platform_routes.platform_login` (`platform_routes.py:40-50`) builds a
`TokenPairResponse` with no `user` and is served with `response_model_exclude_none`, so the
null `user` is stripped. `platform_auth_service.login` (`platform_auth_service.py:79-85`)
mirrors the company-side constant-time + generic-error pattern (dummy-hash verify, single
`InvalidCredentialsError`). No user view leaks; no enumeration on the platform surface.

**Notes / follow-up**

Read-only on the demo platform admin — credentials unchanged, per the suite's
do-not-mutate-demo rule.
