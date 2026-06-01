# TC-OL-060: Suspend-vs-login race — no token issued after the suspend commits

| Field | Value |
|---|---|
| **ID** | TC-OL-060 · **Target** Org Lifecycle (PC-03a) · **Suite** RACE |
| **Type** | Concurrency · **Severity if fail** Medium · **Status** Executed |
| **Result** | ✅ Pass · **Finding tag** CONFIRMS-DOCUMENTED (window bounded by the access-TTL asymmetry) |

## Objective
Characterize the race between a suspend PATCH and concurrent company logins, and assert the invariant: once
the suspend has committed, **no further login mints a token**, and any token minted in the race window is
bounded (it cannot refresh past suspension).

## Break hypothesis
A login that reads `status='active'` just before the suspend commits will mint a token (expected, benign). A
defect would be a login returning a token **after** the suspend committed, or a race-window token that can
refresh indefinitely.

## Steps / Harness
`provision_company("race060")` → fire 60 concurrent `/auth/login` with the suspend PATCH mid-batch (`i==25`)
→ tally → then a fresh login (post-batch) and a refresh of one race-won token. `harness/_finish_race.py` (060).

## Execution result
- **Run at:** 2026-06-01 local · **Result:** ✅ Pass · **Tag:** CONFIRMS-DOCUMENTED

**Evidence**
```
[060] logins 200(won)=30 403(blocked)=29 other=0;
      FRESH login after batch=403 (no token after commit);
      race-won token refresh=403 (window-token bounded, cannot refresh)
```

**Verdict**
Defense held. ~30 logins that read `active` before the suspend committed got tokens; ~29 after got 403; the
mid-batch suspend split them. Crucially: a **fresh** login once the batch settled → 403 (no token is issuable
after the commit), and a **race-won** token's refresh → 403 (it cannot extend past suspension). So the window
is real but **benign and bounded**: a race-window access token works only until its ~15-min TTL on the ungated
access path (TC-OL-004/005) and never refreshes. This is the documented access-token-denylist gap surfacing
under concurrency, not a new defect. CONFIRMS-DOCUMENTED.

**Notes** Bound established by TC-OL-005 (eventual cutoff). No defect unless a token were issued strictly after
the commit — none was.
