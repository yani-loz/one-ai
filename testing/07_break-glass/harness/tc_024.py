async def main():
    n = 50
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "state-024")
        admin = org["admin_access"]
        org_id = org["org_id"]
        print("ORG", org_id, org["slug"])

        # Create n fresh `requested` grants (one platform request each).
        grant_ids = []
        for _ in range(n):
            rq = await request_support(c, plat, org_id)
            rq.raise_for_status()
            grant_ids.append(rq.json()["id"])
        print("CREATED_GRANTS", len(grant_ids))

        approve_results: list = []
        revoke_results: list = []

        async def race(i):
            gid = grant_ids[i]
            # Fire approve + revoke concurrently on the SAME row.
            ar, rr = await asyncio.gather(
                company_approve(c, admin, gid),
                company_revoke(c, admin, gid),
                return_exceptions=True,
            )
            approve_results.append(ar)
            revoke_results.append(rr)

        await fire_concurrent(race, n)

        print("APPROVE_TALLY", summarize(approve_results))
        print("REVOKE_TALLY", summarize(revoke_results))

        # 5xx detector across both result sets.
        def has_5xx(results):
            for r in results:
                code = getattr(r, "status_code", None)
                if isinstance(code, int) and code >= 500:
                    return True
            return False

        print("ANY_5XX", has_5xx(approve_results) or has_5xx(revoke_results))

        # App-level final-status read (ordering-independent) via the inbox.
        inbox = await company_inbox(c, admin)
        rows = inbox.json()
        statuses: dict = {}
        active_count = 0
        for g in rows:
            statuses[g["status"]] = statuses.get(g["status"], 0) + 1
            if g["is_active"]:
                active_count += 1
        print("INBOX_STATUS_TALLY", statuses)
        print("INBOX_ACTIVE_COUNT", active_count)

        print("ASSERT all", n, "revoked:", statuses.get("revoked", 0) == n)
        print("ASSERT zero approved-left-behind:", statuses.get("approved", 0) == 0)
        print("ASSERT zero active:", active_count == 0)
        print("ASSERT revoke always 200:", summarize(revoke_results) == {200: n})
        print("ORG_ID_FOR_PSQL", org_id)


asyncio.run(main())
