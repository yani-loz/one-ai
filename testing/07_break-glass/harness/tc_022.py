async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "state-022")
        admin = org["admin_access"]
        print("ORG", org["org_id"], org["slug"])

        rq = await request_support(c, plat, org["org_id"])
        grant_id = rq.json()["id"]
        print("REQUEST", rq.status_code, "status=", rq.json()["status"])

        ap = await company_approve(c, admin, grant_id)
        bap = ap.json()
        print("APPROVE", ap.status_code, "status=", bap.get("status"), "is_active=", bap.get("is_active"))

        rv = await company_revoke(c, admin, grant_id)
        brv = rv.json()
        print("REVOKE", rv.status_code, "status=", brv.get("status"), "is_active=", brv.get("is_active"))

        ap2 = await company_approve(c, admin, grant_id)
        try:
            bap2 = ap2.json()
        except Exception:
            bap2 = ap2.text
        print("APPROVE-AFTER-REVOKE", ap2.status_code, "body=", bap2)

        rv2 = await company_revoke(c, admin, grant_id)
        try:
            brv2 = rv2.json()
        except Exception:
            brv2 = rv2.text
        print("REVOKE-AGAIN", rv2.status_code, "body=", brv2)

        inbox = await company_inbox(c, admin)
        row = [g for g in inbox.json() if g["id"] == grant_id][0]
        print("FINAL", "status=", row["status"], "is_active=", row["is_active"])

        print("ASSERT approve==200:", ap.status_code == 200)
        print("ASSERT revoke==200:", rv.status_code == 200)
        print("ASSERT approve-after-revoke==409:", ap2.status_code == 409)
        print("ASSERT revoke-again==409:", rv2.status_code == 409)
        print("ASSERT final status revoked & inactive:", row["status"] == "revoked" and row["is_active"] is False)


asyncio.run(main())
