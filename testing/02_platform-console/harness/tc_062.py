async def main():
    # RACE/TC-PC-062 — concurrent same-email onboarding (DIFFERENT-ROW, STRONGEST:
    # rollback-under-race). Pick ONE fresh email E; fire 50 concurrent onboards with
    # DISTINCT slugs but the SAME admin_email E. Expect EXACTLY 1 -> 201, rest -> 409.
    # The win we hunt: the 49 LOSERS each insert their org BEFORE the users.email UNIQUE
    # violation aborts — if the transaction does not roll the org back, an ORPHAN org is
    # committed (service platform_auth_service.py ~line 179 claims it rolls back).
    # psql ground-truth (run AFTER, with the printed prefix):
    #   SELECT count(*) FROM users WHERE email='E';                    -- MUST be 1
    #   SELECT count(*) FROM organizations WHERE slug LIKE '<prefix>%'; -- MUST be 1 (winner only)
    n = 50
    prefix = f"race062-{stamp()}"
    email = f"admin-{prefix}-shared@oneai.dev"  # the SINGLE contested email
    print("RUN-STAMP PREFIX:", prefix)
    print("CONTESTED EMAIL:", email)
    async with _client(timeout=60) as c:
        plat_access, _refresh = await platform_login_pair(c)

        async def attempt(i):
            return await onboard_org(
                c, plat_access,
                name=f"Org {prefix} {i}",
                slug=f"{prefix}-{i}",   # DISTINCT slug for each (so slug is never the collision)
                admin_email=email,       # SAME email for all
                admin_name=f"Admin {i}",
            )

        results = await fire_concurrent(attempt, n)
        tally = summarize(results)
        print("TALLY:", tally)

        sample_201 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 201), None)
        sample_409 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 409), None)
        winner_slug = None
        if sample_201 is not None:
            org = sample_201.json()["organization"]
            winner_slug = org["slug"]
            print("SAMPLE 201 org:", {"id": org["id"], "slug": winner_slug})
            print("SAMPLE 201 admin email:", sample_201.json()["admin"]["email"])
        if sample_409 is not None:
            print("SAMPLE 409 BODY:", sample_409.json())

        n_201 = tally.get(201, 0)
        n_409 = tally.get(409, 0)
        n_500 = tally.get(500, 0)
        exc_keys = [k for k in tally if isinstance(k, str) and k.startswith("EXC")]
        print("COUNTS  201:", n_201, " 409:", n_409, " 500:", n_500, " EXC:", {k: tally[k] for k in exc_keys})
        print("WINNER SLUG (only org that should exist):", winner_slug)

        print("GROUND-TRUTH SQL 1: SELECT count(*) FROM users WHERE email='%s';  -- expect 1" % email)
        print("GROUND-TRUTH SQL 2: SELECT count(*) FROM organizations WHERE slug LIKE '%s%%';  -- expect 1 (no orphans)" % prefix)
        print("GROUND-TRUTH SQL 3 (orphan detail): SELECT slug FROM organizations WHERE slug LIKE '%s%%' ORDER BY slug;" % prefix)

        verdict = (n_201 == 1 and (n_409 + sum(tally[k] for k in exc_keys)) == n - 1 and n_500 == 0)
        if n_201 > 1:
            print("VERDICT: FAIL — >1 user created for one email (UNIQUE BROKEN)")
        elif n_500:
            print("VERDICT: FAIL — server 500 under contention")
        else:
            print("VERDICT:", "PASS-PENDING-PSQL — 1x201 + 49x{409|EXC}; ORPHAN CHECK is SQL 2 (must be 1)"
                  if verdict else "INVESTIGATE — unexpected tally")


asyncio.run(main())
