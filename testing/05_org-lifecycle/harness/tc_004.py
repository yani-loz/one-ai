async def main() -> None:
    print("== TC-OL-004 — /auth/me asymmetry: access token still 200 under suspension (AC4) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "sus004")
        oid = comp["org_id"]
        pre_access = comp["admin_access"]  # access token minted BEFORE suspension
        print("[setup]   org", oid, "pre-suspension access token captured")

        s = await patch_status(c, p_access, oid, STATUS_SUSPENDED)
        print(f"[suspend]  PATCH status=suspended: {s.status_code} status={s.json().get('status')}")

        me = await c.get("/auth/me", headers=bearer(pre_access))
        print(f"[me]       GET /auth/me with pre-suspension access token: {me.status_code} body={me.content!r}")

        await patch_status(c, p_access, oid, STATUS_ACTIVE)  # cleanup

        ok = me.status_code == 200
        print("RESULT:", "PASS — access path is ungated (deliberate asymmetry); /auth/me 200 under suspension"
              if ok else f"FAIL — /auth/me returned {me.status_code}, expected 200")


asyncio.run(main())
