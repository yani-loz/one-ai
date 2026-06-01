# TC-FE-009: Multi-tab / concurrent refresh collision wipes the shared session 🆕

| Field | Value |
|---|---|
| **ID** | TC-FE-009 |
| **Target** | Frontend (identity auth client — token rotation) |
| **Suite** | Session lifecycle / concurrency |
| **Type** | Concurrency / Adversarial |
| **Severity if it fails** | Medium (spurious cross-tab logout; valid session destroyed) |
| **Status** | Executed |
| **Result** | ❌ Fail (defect reproduced — the win) |
| **Finding tag** | NEW |

## Objective
Two browser contexts (tabs) that share the company session must not be able to log each other out. The
single-flight guard (AUD-11) should keep concurrent refreshes from revoking each other's token.

## Break hypothesis
`refreshInFlight` (the AUD-11 single-flight promise) is a **module-level** variable — it dedupes
concurrent refreshes **within one tab only**. The company refresh token lives in **shared `localStorage`**.
So two tabs can both read the same token `T1` and both `POST /auth/refresh(T1)`: one wins (rotates `T1→T2`,
stores `T2`), the loser gets 401 and runs `setTokens(null)` → `localStorage.removeItem(...)`, **wiping the
winner's freshly-stored valid token** → both tabs are logged out, and a reload lands on `/login`.

## Preconditions
Authenticated company session (`admin@demo.oneai`). Two concurrent refreshes of the same stored token,
modelled faithfully at the real server + the exact shared-storage operations the app performs.

## Steps
1. Authenticated company session; read the stored token `T1`.
2. **Winner** (tab A): `POST /auth/refresh(T1)` → `T2`; persist `T2` (= `authClient.setTokens(pair)` → `setItem`).
3. **Loser** (tab B): `POST /auth/refresh(T1)` → 401 (T1 now revoked); run `setTokens(null)` → `removeItem`
   (the exact failure path at `authClient.ts:64-68`).
4. Inspect shared storage; confirm the wiped `T2` was a **live** token; then reload to show the user-visible result.

## Expected result (contract)
A losing concurrent refresh must NOT destroy another tab's valid session. (It does — hence Fail.)

## Harness
Playwright MCP `browser_evaluate` against the live backend (`/auth/refresh` rotation) replicating the two
tabs' real storage operations + a reload to confirm the logout.

---

## Execution result

- **Run at:** 2026-06-01 ~11:20 local
- **Result:** ❌ Fail (defect reproduced)
- **Finding tag:** NEW (Medium) — not in `docs/FIX_BEFORE_PROD.md`; an extension of the AUD-11 single-flight fix

**Actual behavior**
> The losing tab's 401 handler wiped the winner's freshly-rotated, **valid** token out of shared
> `localStorage`. A subsequent reload logged the session out (`/login`).

**Evidence**
```
winner_refresh_status: 200            # T1 -> T2 (74RdK-sx…), winner persisted T2 to shared storage
storage_after_winner: "74RdK-sx"      # a VALID token is in localStorage
loser_refresh_status: 401             # loser presented the same captured T1 (now revoked)
storage_after_loser: "EMPTY (wiped!)" # loser's setTokens(null) -> removeItem wiped the winner's T2
wiped_token_T2_was_valid: 200         # T2 still rotated => a LIVE session token was destroyed
post-wipe reload: http://localhost:5173/  ->  /login   (forced logout)
```

**Verdict**
Contract violated. Root cause: the single-flight dedupe is per-tab (`authClient.ts` module-level
`refreshInFlight`), so it gives **no** cross-tab protection; and the failure path nulls the **shared**
storage key unconditionally (`setTokens(null)` → `localStorage.removeItem(REFRESH_STORAGE_KEY)`,
`authClient.ts:64-68`). Prod-reachable and **StrictMode-independent**: two tabs whose access tokens have
both expired (e.g. after idle), then interacted-with/reloaded together, both refresh the same stored token
→ one wins, the loser wipes the shared session.

**Timing window (honest triage):** because the real `performRefresh` re-reads `getStoredRefreshToken()` at
call time, the loser only collides if **both tabs read the same token within the refresh round-trip
(~tens of ms)** before either writes the rotation back; outside that window the second tab reads the
already-rotated token and succeeds. The repro above faithfully forces that both-captured-`T1` sub-case
(it *is* the real race) but bypasses the re-read that often saves the loser — so this is a genuine but
**narrow** window, which puts the severity at the top of a Low–Medium band. (Single-flight *does* robustly
protect the single-**tab** case — `refreshInFlight` is set synchronously before the inner fetch yields — so
the lone organic dev `/auth/refresh→401` seen in TC-FE-004 is *consistent with* this concurrent-refresh
hazard class but its exact trigger was **not** isolated; it is not claimed to be a StrictMode double-mount.)

**Severity:** Medium. Not a security breach (no token leak, no auth bypass) — a **correctness/UX defect**:
unexpected logout of a valid session, in a product where an operator keeping multiple console tabs open is
normal.

**Remediation**
Compare-and-clear (cheap, narrows it): in `performRefresh`'s failure branch, only `setTokens(null)` if the
**currently stored** token still equals the one whose refresh just failed (another tab may have rotated it
meanwhile — adopt the new value and retry instead of wiping). Note this is *check-then-act* and not atomic,
so it shrinks but does not fully close the window. The **robust** fix is to coordinate refresh across tabs
with the Web Locks API (`navigator.locks.request`) or a `BroadcastChannel`/`storage`-event leader, so only
one tab rotates and the others read the result. (Targeting an httpOnly refresh **cookie** — the tracked
deferral — also dissolves it, since the browser, not JS, owns the single token.)

**Notes / follow-up**
A real two-tab timing race (both reloaded within the ~10–30 ms refresh round-trip) is the organic trigger;
the deterministic repro above proves the mechanism without depending on that window. Re-test after the fix
with two real tabs.
