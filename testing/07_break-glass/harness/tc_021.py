async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "state-021")
        admin = org["admin_access"]
        print("ORG", org["org_id"], org["slug"])

        rq = await request_support(c, plat, org["org_id"])
        grant_id = rq.json()["id"]
        print("REQUEST", rq.status_code, "status=", rq.json()["status"])

        d = await company_deny(c, admin, grant_id)
        bd = d.json()
        print("DENY", d.status_code, "status=", bd.get("status"), "is_active=", bd.get("is_active"))

        ap = await company_approve(c, admin, grant_id)
        try:
            bap = ap.json()
        except Exception:
            bap = ap.text
        print("APPROVE-AFTER-DENY", ap.status_code, "body=", bap)

        rv = await company_revoke(c, admin, grant_id)
        try:
            brv = rv.json()
        except Exception:
            brv = rv.text
        print("REVOKE-AFTER-DENY", rv.status_code, "body=", brv)

        inbox = await company_inbox(c, admin)
        row = [g for g in inbox.json() if g["id"] == grant_id][0]
        print("FINAL", "status=", row["status"], "is_active=", row["is_active"])

        print("ASSERT deny==200:", d.status_code == 200)
        print("ASSERT approve-after-deny==409:", ap.status_code == 409)
        print("ASSERT revoke-after-deny==409:", rv.status_code == 409)
        print("ASSERT final status denied:", row["status"] == "denied")


asyncio.run(main())
