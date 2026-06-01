async def main():
    # RACE/TC-PC-065 — rotation-chain integrity. Serially rotate a refresh token 20x
    # (each new token taken from the prior 200 response). Then assert EVERY one of the
    # 19 prior tokens -> 401 (already consumed) and only the FINAL token still works (200).
    # Proves single-use holds end-to-end across a rotation chain (no resurrected ancestor).
    chain_len = 20
    chain = []  # raw refresh tokens in rotation order; chain[-1] is the only live one
    async with _client(timeout=60) as c:
        _access, refresh = await platform_login_pair(c)
        chain.append(refresh)

        for step in range(chain_len):
            r = await c.post("/platform/refresh", json={"refresh_token": chain[-1]})
            if r.status_code != 200:
                print(f"CHAIN BROKE at step {step}: status={r.status_code} body={r.json()}")
                print("VERDICT: FAIL — rotation chain could not reach length", chain_len)
                return
            chain.append(r.json()["refresh_token"])

        print("CHAIN LENGTH (incl. original):", len(chain))
        final_token = chain[-1]
        priors = chain[:-1]  # 20 ancestors (original + 19 intermediates) — all must be dead
        print("PRIOR (must-be-dead) tokens:", len(priors), " FINAL (must-live) token: 1")

        # Replay every prior -> all must 401.
        prior_codes = {}
        first_live_prior = None
        for idx, tok in enumerate(priors):
            rr = await c.post("/platform/refresh", json={"refresh_token": tok})
            prior_codes[rr.status_code] = prior_codes.get(rr.status_code, 0) + 1
            if rr.status_code != 401 and first_live_prior is None:
                first_live_prior = {"index": idx, "status": rr.status_code, "body": rr.json()}
        print("PRIOR REPLAY CODES (all should be 401):", prior_codes)
        if first_live_prior is not None:
            print("RESURRECTED ANCESTOR (should be None):", first_live_prior)

        # The final token must still rotate (200). NOTE: this CONSUMES it, issuing a 21st
        # token — fine; we only assert it returned 200 vs the priors' 401.
        fr = await c.post("/platform/refresh", json={"refresh_token": final_token})
        print("FINAL TOKEN rotate ->", fr.status_code, " body keys:", sorted(fr.json().keys()) if fr.status_code == 200 else fr.json())

        all_priors_dead = set(prior_codes.keys()) == {401}
        final_lives = fr.status_code == 200
        verdict = all_priors_dead and final_lives and first_live_prior is None
        print("ALL PRIORS DEAD (401):", all_priors_dead, " FINAL LIVES (200):", final_lives)
        print("VERDICT:", "PASS — single-use chain holds; only the tip is live"
              if verdict else "FAIL — an ancestor resurrected or the tip was dead")


asyncio.run(main())
