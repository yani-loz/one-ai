async def main():
    # RACE/TC-PC-060 — platform refresh single-use under SAME-ROW contention.
    # Login ONCE (demo admin) to get exactly ONE refresh token; fire 60 concurrent
    # POST /platform/refresh ALL presenting that SAME token. Single-use => EXACTLY
    # 1 -> 200 and 59 -> 401. >1 success would REFUTE the single-use guarantee.
    # CAVEAT (state in verdict): this serializes on the conditional UPDATE ... WHERE
    # revoked_at IS NULL row lock by design, so a clean result CORROBORATES (confirmed
    # by code review of token_rotator.consume) — it is not independent proof.
    n = 60
    async with _client(timeout=60) as c:
        _access, refresh = await platform_login_pair(c)
        print("MINTED one platform refresh token; firing", n, "concurrent /platform/refresh")

        async def attempt(_i):
            return await c.post("/platform/refresh", json={"refresh_token": refresh})

        results = await fire_concurrent(attempt, n)
        tally = summarize(results)
        print("TALLY:", tally)

        # One sample body per outcome (codes AND bodies — not paraphrase).
        sample_200 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 200), None)
        sample_401 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 401), None)
        if sample_200 is not None:
            print("SAMPLE 200 BODY keys:", sorted(sample_200.json().keys()))
        if sample_401 is not None:
            print("SAMPLE 401 BODY:", sample_401.json())

        n_200 = tally.get(200, 0)
        n_401 = tally.get(401, 0)
        n_500 = tally.get(500, 0)
        exc_keys = [k for k in tally if isinstance(k, str) and k.startswith("EXC")]
        print("COUNTS  200:", n_200, " 401:", n_401, " 500:", n_500, " EXC:", {k: tally[k] for k in exc_keys})

        # The winner's new refresh token must itself rotate (proves a real new pair was issued).
        follow_ok = None
        if sample_200 is not None:
            new_refresh = sample_200.json()["refresh_token"]
            fr = await c.post("/platform/refresh", json={"refresh_token": new_refresh})
            follow_ok = fr.status_code
            print("FOLLOW-UP rotate of winner's new token ->", follow_ok)

        verdict = (n_200 == 1 and n_401 == n - 1 and n_500 == 0 and not exc_keys)
        if n_200 > 1:
            print("VERDICT: FAIL — single-use BROKEN, >1 success (REFUTES_FIX)")
        elif n_500 or exc_keys:
            print("VERDICT: FAIL — server 500 / client EXC under contention")
        else:
            print("VERDICT:", "PASS — exactly 1x200 + 59x401 (corroborates single-use, same-row caveat)"
                  if verdict else "INVESTIGATE — unexpected tally")


asyncio.run(main())
