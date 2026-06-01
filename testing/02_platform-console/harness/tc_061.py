async def main():
    # RACE/TC-PC-061 — concurrent same-slug onboarding (DIFFERENT-ROW, independently
    # provable). Pick ONE fresh run-stamped slug S; fire 50 concurrent POST /platform/orgs
    # all with slug S but DISTINCT admin emails. Expect EXACTLY 1 -> 201 and 49 -> 409
    # (or a captured EXC). The 49 UNIQUE-violation 409s are the POSITIVE CONTROL proving
    # contention truly engaged. psql ground-truth (run AFTER, with the printed prefix):
    #   SELECT count(*) FROM organizations WHERE slug='S';  -- MUST be 1
    n = 50
    prefix = f"race061-{stamp()}"
    slug = f"{prefix}-s"  # the SINGLE contested slug
    print("RUN-STAMP PREFIX:", prefix)
    print("CONTESTED SLUG:", slug)
    async with _client(timeout=60) as c:
        plat_access, _refresh = await platform_login_pair(c)

        async def attempt(i):
            return await onboard_org(
                c, plat_access,
                name=f"Org {slug} {i}",
                slug=slug,                                   # SAME slug for all
                admin_email=f"admin-{prefix}-{i}@oneai.dev",  # DISTINCT emails
                admin_name=f"Admin {i}",
            )

        results = await fire_concurrent(attempt, n)
        tally = summarize(results)
        print("TALLY:", tally)

        sample_201 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 201), None)
        sample_409 = next((r for r in results if not isinstance(r, BaseException) and r.status_code == 409), None)
        if sample_201 is not None:
            org = sample_201.json()["organization"]
            print("SAMPLE 201 org:", {"id": org["id"], "slug": org["slug"], "user_count": org["user_count"]})
        if sample_409 is not None:
            print("SAMPLE 409 BODY:", sample_409.json())

        n_201 = tally.get(201, 0)
        n_409 = tally.get(409, 0)
        n_500 = tally.get(500, 0)
        exc_keys = [k for k in tally if isinstance(k, str) and k.startswith("EXC")]
        print("COUNTS  201:", n_201, " 409:", n_409, " 500:", n_500, " EXC:", {k: tally[k] for k in exc_keys})

        print("GROUND-TRUTH SQL: SELECT count(*) FROM organizations WHERE slug='%s';  -- expect 1" % slug)

        verdict = (n_201 == 1 and (n_409 + sum(tally[k] for k in exc_keys)) == n - 1 and n_500 == 0)
        if n_201 > 1:
            print("VERDICT: FAIL — >1 org created for one slug (UNIQUE/race BROKEN)")
        elif n_500:
            print("VERDICT: FAIL — server 500 under contention")
        else:
            print("VERDICT:", "PASS-PENDING-PSQL — 1x201 + 49x{409|EXC}; confirm count(*)=1 via psql"
                  if verdict else "INVESTIGATE — unexpected tally")


asyncio.run(main())
