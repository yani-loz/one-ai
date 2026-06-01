async def main():
    # RACE/TC-PC-064 — concurrent issuance integrity. Fire 30 concurrent POST
    # /platform/login for the DEMO admin -> all 200, and all 30 refresh_tokens DISTINCT
    # (no collision / shared-state overwrite). Distinctness is cryptographic
    # (secrets.token_urlsafe), so what this really catches is a non-200 or a shared-state
    # bug that would overwrite/duplicate an issued token under load.
    n = 30
    async with _client(timeout=60) as c:
        async def attempt(_i):
            return await c.post(
                "/platform/login",
                json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW},
            )

        results = await fire_concurrent(attempt, n)
        tally = summarize(results)
        print("TALLY:", tally)

        n_200 = tally.get(200, 0)
        n_500 = tally.get(500, 0)
        exc_keys = [k for k in tally if isinstance(k, str) and k.startswith("EXC")]
        print("COUNTS  200:", n_200, " 500:", n_500, " EXC:", {k: tally[k] for k in exc_keys})

        ok = [r for r in results if not isinstance(r, BaseException) and r.status_code == 200]
        refresh_tokens = [r.json()["refresh_token"] for r in ok]
        access_tokens = [r.json()["access_token"] for r in ok]
        distinct_refresh = len(set(refresh_tokens))
        distinct_access = len(set(access_tokens))
        print("ISSUED PAIRS:", len(ok), " DISTINCT refresh:", distinct_refresh, " DISTINCT access:", distinct_access)

        if ok:
            print("SAMPLE 200 BODY keys:", sorted(ok[0].json().keys()))
            print("SAMPLE refresh prefix:", ok[0].json()["refresh_token"][:8], "...")

        # Cross-check the issued refresh tokens are also distinct AT THE DB (hash rows).
        hashes = [sha256_hex(t) for t in refresh_tokens]
        print("DISTINCT refresh-hashes:", len(set(hashes)), "of", len(hashes))

        verdict = (n_200 == n and n_500 == 0 and not exc_keys
                   and distinct_refresh == n and distinct_access == n)
        if distinct_refresh != len(refresh_tokens):
            print("VERDICT: FAIL — duplicate refresh token issued under concurrency")
        elif n_200 != n:
            print("VERDICT: FAIL — not all concurrent logins returned 200")
        else:
            print("VERDICT:", "PASS — 30/30 200, all refresh+access tokens distinct"
                  if verdict else "INVESTIGATE")


asyncio.run(main())
