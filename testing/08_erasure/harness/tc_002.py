async def main():
    import asyncpg  # _common.py provides no DB helper; asyncpg used for atomic ground-truth reads

    db = await asyncpg.connect(host="db", user="oneai", password="oneai", database="oneai", port=5432)

    async def erase_with_pw(c, plat_token, org_id, *, confirm_slug, password, reason="GDPR offboarding (test)"):
        return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                            json={"reason": reason, "confirm_slug": confirm_slug, "password": password})

    async def gt(org_id):
        users = await db.fetchval("SELECT count(*) FROM users WHERE org_id=$1", org_id)
        status = await db.fetchval("SELECT status FROM organizations WHERE id=$1", org_id)
        erased_rows = await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action='org.erased' AND org_id=$1", org_id)
        return {"users": users, "status": status, "erased_rows": erased_rows}

    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "hold-er2")
        org_id = org["org_id"]
        slug = org["slug"]
        oid = __import__("uuid").UUID(org_id)

        before = await gt(oid)
        print("BASELINE:", before)
        assert before["users"] == 1 and before["status"] == "active", "setup failed"

        wrong = f"not-the-slug-{stamp()}"
        er = await erase_with_pw(c, plat, org_id, confirm_slug=wrong, password=PLATFORM_PW)
        print("ERASE (wrong slug):", er.status_code, er.text[:400])

        after = await gt(oid)
        print("AFTER:", after)

        got_400 = er.status_code == 400
        untouched = (after["users"] == before["users"] and after["status"] == "active"
                     and after["erased_rows"] == 0)
        print("RESULT 400:", got_400, "| nothing_touched:", untouched)
        print("VERDICT:", "PASS" if (got_400 and untouched) else "FAIL")
        print("ORG_ID:", org_id, "SLUG:", slug)

    await db.close()


asyncio.run(main())
