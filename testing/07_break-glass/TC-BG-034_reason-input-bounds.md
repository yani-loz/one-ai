# TC-BG-034: Input bounds — empty reason, >500 reason, extra field all rejected (422)

| Field | Value |
|---|---|
| **ID** | TC-BG-034 · **Suite** AEA · **Type** Boundary/Negative · **Severity if fail** Medium |
| **Result** | ✅ Pass · **Tag** ✔ CONFIRMS-FIXED · **Status** Executed |

## Execution result (2026-06-01)
**Evidence**
```
empty reason → 422 string_too_short | 501-char → 422 string_too_long
extra {status:approved} → 422 extra_forbidden loc=['body','status'] | grants created on A: 0
```
**Verdict:** Defense held. `SupportAccessRequest` enforces `Field(min_length=1, max_length=500)` +
`extra='forbid'` (`support_schemas.py:21-26`), so a smuggled privileged field is rejected loudly
(mass-assignment closed), not silently dropped. Extends PR-5 review #5.
