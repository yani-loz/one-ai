async def main() -> None:
    print("== TC-OL-021 — company-aud token (real admin sub) -> PATCH status, 401 + NO write ==")
    async with _client() as c:
        plat = await platform_login(c)
        me = await c.get("/platform/me", headers=bearer(plat))
        real_admin_id = me.json()["id"]

        comp = await provision_company(c, plat, "xdom")
        oid = comp["org_id"]
        before = await get_org_detail(c, plat, oid)
        print(f"[setup]   target org: {comp['slug']} ({oid}) status_before={before.json()['status']}")

        forged = forge_company_token(sub=real_admin_id, org_id=None)
        attack = await patch_status(c, forged, oid, STATUS_SUSPENDED)
        print(f"[attack]  PATCH /status {{suspended}} (FORGED company-aud token): {attack.status_code}")
        print(f"          body: {attack.json()}")

        after = await get_org_detail(c, plat, oid)
        status_after = after.json()["status"]
        print(f"[readback] GET detail (real platform token): {after.status_code} status_after={status_after}")

        ok = attack.status_code == 401 and status_after == STATUS_ACTIVE
        print(f"RESULT: {'PASS' if ok else 'FAIL'} — "
              f"{'401 at auth dependency; status unchanged (active) => no write' if ok else 'UNEXPECTED — see codes/status above'}")


asyncio.run(main())
