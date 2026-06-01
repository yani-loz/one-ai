async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract39")
        oid, email = comp["org_id"], comp["admin_email"]

        # Set legal_hold true on MY org.
        r = await patch_legal_hold(c, p_access, oid, True)
        print(f"PATCH legal_hold=true -> {r.status_code} legal_hold={r.json().get('legal_hold')}")

        # A company user of that org must STILL log in (legal hold is auth-inert today).
        relog = await login(c, email, DEFAULT_PW)
        print(f"company login under legal_hold=true -> {relog.status_code} (expect 200)")

        # Confirm refresh path also unaffected by legal hold.
        if relog.status_code == 200:
            rt = relog.json()["refresh_token"]
            rr = await c.post("/auth/refresh", json={"refresh_token": rt})
            print(f"refresh under legal_hold=true -> {rr.status_code} (expect 200)")

        # Clean up.
        await patch_legal_hold(c, p_access, oid, False)


asyncio.run(main())
