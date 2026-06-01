

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "consent-bg004")
        org_id = org["org_id"]
        admin_token = org["admin_access"]
        admin_email = org["admin_email"]
        print("== ORG ==", org_id, org["slug"])
        print("== REAL admin email ==", admin_email)

        # 1. Platform admin REQUESTS.
        r = await request_support(c, plat, org_id)
        grant_id = r.json()["id"]
        print("== REQUEST ==", r.status_code, "grant", grant_id, "status", r.json()["status"])

        # 2. REAL company_admin approves (the genuine consent).
        ap = await company_approve(c, admin_token, grant_id)
        body = ap.json()
        print("== REAL approve status ==", ap.status_code)
        print("status      :", body["status"])
        print("is_active   :", body["is_active"])
        print("decided_at  :", body["decided_at"])
        print("decided_by  :", body["decided_by_email"])
        print("expires_at  :", body["expires_at"])

        # Window check: expires_at should be ~4h after decided_at.
        di = datetime.fromisoformat(body["decided_at"].replace("Z", "+00:00"))
        ei = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        delta_h = (ei - di).total_seconds() / 3600
        print("== window hours (expires_at - decided_at) ==", delta_h)
        print("== FULL BODY ==", body)

asyncio.run(main())
