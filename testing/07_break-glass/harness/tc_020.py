async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "state-020")
        admin = org["admin_access"]
        print("ORG", org["org_id"], org["slug"])

        rq = await request_support(c, plat, org["org_id"])
        grant_id = rq.json()["id"]
        print("REQUEST", rq.status_code, "status=", rq.json()["status"])

        a1 = await company_approve(c, admin, grant_id)
        b1 = a1.json()
        print("APPROVE#1", a1.status_code, "status=", b1.get("status"),
              "is_active=", b1.get("is_active"), "expires_at=", b1.get("expires_at"))

        a2 = await company_approve(c, admin, grant_id)
        try:
            b2 = a2.json()
        except Exception:
            b2 = a2.text
        print("APPROVE#2", a2.status_code, "body=", b2)

        # Re-read via inbox to confirm nothing mutated by the rejected call.
        inbox = await company_inbox(c, admin)
        row = [g for g in inbox.json() if g["id"] == grant_id][0]
        print("FINAL", "status=", row["status"], "is_active=", row["is_active"],
              "expires_at=", row["expires_at"])

        print("ASSERT approve#1==200:", a1.status_code == 200)
        print("ASSERT approve#2==409:", a2.status_code == 409)
        print("ASSERT expires_at unchanged:", row["expires_at"] == b1.get("expires_at"))


asyncio.run(main())
