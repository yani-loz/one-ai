# TC-BG-004: A real company approve records decider attribution + the time box

| Field | Value |
|---|---|
| **ID** | TC-BG-004 · **Suite** CONSENT · **Type** Positive · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** — (NA, contract) · **Status** Executed |

## Objective
A *real* company_admin approve fully attributes the consent: who, when, until-when — the deliberate contrast
to the forged phantom (TC-BG-003).

## Execution result (2026-06-01)
**Evidence**
```
REAL approve 200 | status=approved is_active=True decided_by=admin-consent-bg004-...@oneai.dev
window hours (expires_at - decided_at) == 4.0 | psql: decided_by_email set, has_user_id=t, expires_at set
```
**Verdict:** PC-05-AC2 (attribution) holds. `_stamp_decision` (`company_support_service.py:140-147`) records
WHO (email + user_id) + WHEN; `expires_at = now + 4h` (`:80-83`). A real token yields real attribution; the
forged token (TC-BG-003) yields a null decider. Defense held.
