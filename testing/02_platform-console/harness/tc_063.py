async def main():
    # RACE/TC-PC-063 — logout-vs-refresh race on the SAME token. Repeat ~50 iterations:
    # each iteration mints a FRESH platform token, fires POST /platform/logout and
    # POST /platform/refresh concurrently on it, then does a SEQUENTIAL follow-up refresh.
    # Invariants:
    #   - NEVER a 500 (logout and refresh both touch the same row's conditional UPDATE).
    #   - logout ALWAYS 204 (idempotent).
    #   - refresh is 200 XOR 401 (at most one of {logout,refresh} "consumed" the token).
    #   - follow-up refresh is ALWAYS 401 (token is dead after the pair, either way).
    iters = 50
    logout_codes = {}
    refresh_codes = {}
    followup_codes = {}
    pair_500 = 0
    pair_exc = 0
    anomalies = []
    async with _client(timeout=60) as c:
        for it in range(iters):
            _access, refresh = await platform_login_pair(c)

            async def do_logout():
                return await c.post("/platform/logout", json={"refresh_token": refresh})

            async def do_refresh():
                return await c.post("/platform/refresh", json={"refresh_token": refresh})

            pair = await asyncio.gather(do_logout(), do_refresh(), return_exceptions=True)
            lo, re = pair

            if isinstance(lo, BaseException) or isinstance(re, BaseException):
                pair_exc += 1
                anomalies.append({"iter": it, "exc": [type(lo).__name__ if isinstance(lo, BaseException) else None,
                                                       type(re).__name__ if isinstance(re, BaseException) else None]})
                continue

            logout_codes[lo.status_code] = logout_codes.get(lo.status_code, 0) + 1
            refresh_codes[re.status_code] = refresh_codes.get(re.status_code, 0) + 1
            if lo.status_code == 500 or re.status_code == 500:
                pair_500 += 1
                anomalies.append({"iter": it, "logout": lo.status_code, "refresh": re.status_code,
                                  "logout_body": _safe_body(lo), "refresh_body": _safe_body(re)})

            # Follow-up: the token must be dead now regardless of who won.
            fu = await c.post("/platform/refresh", json={"refresh_token": refresh})
            followup_codes[fu.status_code] = followup_codes.get(fu.status_code, 0) + 1
            if fu.status_code != 401:
                anomalies.append({"iter": it, "FOLLOWUP_NOT_401": fu.status_code, "body": _safe_body(fu)})

    print("LOGOUT CODES   :", logout_codes)
    print("REFRESH CODES  :", refresh_codes)
    print("FOLLOWUP CODES :", followup_codes)
    print("PAIR 500 COUNT :", pair_500, " PAIR EXC COUNT:", pair_exc)
    print("ANOMALIES (first 10):", anomalies[:10])

    no_500 = pair_500 == 0 and pair_exc == 0
    logout_all_204 = set(logout_codes.keys()) <= {204}
    refresh_only_200_401 = set(refresh_codes.keys()) <= {200, 401}
    followup_all_401 = set(followup_codes.keys()) == {401}
    both_orderings_fired = refresh_codes.get(200, 0) > 0 and refresh_codes.get(401, 0) > 0

    print("NO 500/EXC:", no_500, " LOGOUT ALL 204:", logout_all_204,
          " REFRESH IN {200,401}:", refresh_only_200_401, " FOLLOWUP ALL 401:", followup_all_401)
    print("BOTH ORDERINGS OBSERVED (refresh won sometimes, lost sometimes):", both_orderings_fired)

    verdict = no_500 and logout_all_204 and refresh_only_200_401 and followup_all_401
    print("VERDICT:", "PASS — no 500; token dead post-pair; at most one consumer"
          if verdict else "FAIL — see anomalies")


def _safe_body(r):
    try:
        return r.json()
    except Exception:
        return r.text[:200]


asyncio.run(main())
