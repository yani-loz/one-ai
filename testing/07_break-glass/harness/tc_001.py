

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "consent-bg001")
        org_id = org["org_id"]
        print("== ORG ==", org_id, org["slug"])

        r = await request_support(c, plat, org_id)
        body = r.json()
        print("== REQUEST status ==", r.status_code)
        print("status      :", body["status"])
        print("is_active   :", body["is_active"])
        print("expires_at  :", body["expires_at"])
        print("decided_at  :", body["decided_at"])
        print("decided_by  :", body["decided_by_email"])
        print("requested_by:", body["requested_by_email"])
        print("reason      :", body["reason"])
        print("grant_id    :", body["id"])
        print("== FULL BODY ==", body)

asyncio.run(main())
