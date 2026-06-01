async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "aea31")
        print(f"1) provisioned A={a['org_id']} admin_email={a['admin_email']}")

        req = await request_support(c, plat, a["org_id"], reason="live-expiry target")
        gid = req.json()["id"]
        ap = await company_approve(c, a["admin_access"], gid)
        b = ap.json()
        print(f"2) approve: {ap.status_code} status={b['status']} is_active={b['is_active']} "
              f"expires_at={b['expires_at']} (future)")
        print(f"GRANT_ID={gid}")
        print(f"ADMIN_EMAIL={a['admin_email']}")


asyncio.run(main())
