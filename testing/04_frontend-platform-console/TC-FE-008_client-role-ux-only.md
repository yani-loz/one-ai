# TC-FE-008: Client role is UX-only — the server stays authoritative

| Field | Value |
|---|---|
| **ID** | TC-FE-008 |
| **Target** | Frontend guards + backend audience gate |
| **Suite** | Authorization model |
| **Type** | Adversarial |
| **Severity if it fails** | Critical (client-trusted authorization) |
| **Status** | Executed |
| **Result** | ✅ Pass |
| **Finding tag** | CONFIRMS-FIXED |

## Objective
The client-side role gates (`PlatformRoute`/`RoleHome`) are **UX only**. Even if a client could be
coerced into rendering the console shell, the API independently rejects a non-platform token — so no
platform *data* is ever reachable without a real `aud='platform'` token.

## Break hypothesis
The console trusts the client-side role: spoofing the client role (or reaching the shell) yields real
platform data from the API.

## Steps
1. (Client layer) Confirm a non-platform principal is redirected away from `/platform` — TC-FE-003c.
2. (Server layer) Confirm the API rejects a company token / missing token on `/platform/*` regardless of
   any client state — Target 02 TC-PC-023 (company token → `GET`/`POST /platform/orgs` → 401) and
   TC-PC-030 (missing bearer → 401).

## Expected result
Two independent layers: client redirect (UX) + server 401 (authoritative). Platform data requires a
real platform token, never a client role flag.

## Harness
Cross-layer: Playwright (TC-FE-003c redirect) + the live backend evidence from Target 02.

---

## Execution result

- **Run at:** 2026-06-01 (composed from TC-FE-003c + Target 02 live runs)
- **Result:** ✅ Pass
- **Finding tag:** CONFIRMS-FIXED

**Actual behavior / Evidence**
```
CLIENT (TC-FE-003c): authenticated company admin → /platform → redirected to / (never renders console)
SERVER (Target 02):  company admin token → GET /platform/orgs → 401; → POST /platform/orgs → 401 (TC-PC-023)
                     missing bearer → GET /platform/me → 401 not 403 (TC-PC-030)
                     forged COMPANY-aud token w/ real admin sub → /platform/me → 401 (TC-PC-020, discriminating)
```

**Verdict**
Defense held — and it is **two-layer**. `PlatformRoute`'s own docstring states the role check "is a UX
gate ONLY; real authorization is enforced server-side by the aud='platform' JWT claim." The backend pass
proved that gate is load-bearing: a company token (even with a spoofed `role=platform_admin`, even with a
real admin `sub`) is rejected by the audience check before any handler runs. So the client role flag is
never trusted for data access.

**Notes / follow-up**
Not driven as a separate browser interception (forging client React state adds nothing over the
authoritative server evidence). The server-side proof is the live Target 02 cross-domain suite.
