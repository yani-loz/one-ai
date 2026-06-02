# TC-BG-031: Live expiry — backdating `expires_at` flips `is_active` false while status stays `approved`

| Field | Value |
|---|---|
| **ID** | TC-BG-031 · **Suite** AEA · **Type** Boundary/Adversarial (psql-assisted) · **Severity if fail** High |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
approve: is_active=True expires_at(future)
psql: UPDATE support_grant SET expires_at = now() - interval '1 hour'
inbox re-read: status=approved is_active=False (the clock decides)
```
**Verdict:** Defense held. `grant_is_active` recomputes `approved AND now < expires_at` on every read
(`support_grant_view.py:25-30`) — no stored flag, no sweeper. An expired grant confers no access while its
`status` stays `approved`. PC-05-AC7.
