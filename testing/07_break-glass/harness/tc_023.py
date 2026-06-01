async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "state-023")
        admin = org["admin_access"]
        print("ORG", org["org_id"], org["slug"])

        rq = await request_support(c, plat, org["org_id"])
        grant_id = rq.json()["id"]
        print("REQUEST", rq.status_code, "status=", rq.json()["status"])

        ap = await company_approve(c, admin, grant_id)
        bap = ap.json()
        print("APPROVE", ap.status_code, "status=", bap.get("status"), "is_active=", bap.get("is_active"))

        d = await company_deny(c, admin, grant_id)
        try:
            bd = d.json()
        except Exception:
            bd = d.text
        print("DENY-AFTER-APPROVE", d.status_code, "body=", bd)

        inbox = await company_inbox(c, admin)
        row = [g for g in inbox.json() if g["id"] == grant_id][0]
        print("FINAL", "status=", row["status"], "is_active=", row["is_active"])

        print("ASSERT approve==200:", ap.status_code == 200)
        print("ASSERT deny-after-approve==409:", d.status_code == 409)
        print("ASSERT final status still approved & active:", row["status"] == "approved" and row["is_active"] is True)


asyncio.run(main())
