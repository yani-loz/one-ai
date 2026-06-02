# TC-BG-001: A request starts `requested` (never `approved`) and records the requester email

| Field | Value |
|---|---|
| **ID** | TC-BG-001 · **Suite** CONSENT · **Type** Positive · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** — (NA, original-design AC) · **Status** Executed |

## Objective
A break-glass request opens strictly in pending `requested` (consent not yet given), with the requester email
captured for attribution.

## Execution result (2026-06-01)
**Evidence**
```
REQUEST 201 | status=requested is_active=False expires_at=None decided_by=None requested_by=super@ethera.ai
psql: status=requested, expires_at NULL, decided_by_email NULL, requested_by_email=super@ethera.ai
```
**Verdict:** PC-05-AC1 holds. `request_access` (`platform_support_service.py:71-79`) writes the requester
email at write time; consent is structurally pending (`is_active=false`, no time box). Defense held.
