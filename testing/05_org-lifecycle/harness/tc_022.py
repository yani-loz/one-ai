async def main() -> None:
    print("== TC-OL-022 — company-aud token (real admin sub) -> PATCH legal-hold, 401 + NO write ==")
    async with _client() as c:
        plat = await platform_login(c)
        me = await c.get("/platform/me", headers=bearer(plat))
        real_admin_id = me.json()["id"]

        comp = await provision_company(c, plat, "xdom")
        oid = comp["org_id"]
        before = await get_org_detail(c, plat, oid)
        print(f"[setup]   target org: {comp['slug']} ({oid}) legal_hold_before={before.json()['legal_hold']}")

        forged = forge_company_token(sub=real_admin_id, org_id=None)
        attack = await patch_legal_hold(c, forged, oid, True)
        print(f"[attack]  PATCH /legal-hold {{true}} (FORGED company-aud token): {attack.status_code}")
        print(f"          body: {attack.json()}")

        after = await get_org_detail(c, plat, oid)
        lh_after = after.json()["legal_hold"]
        print(f"[readback] GET detail (real platform token): {after.status_code} legal_hold_after={lh_after}")

        ok = attack.status_code == 401 and lh_after is False
        print(f"RESULT: {'PASS' if ok else 'FAIL'} — "
              f"{'401 at auth dependency; legal_hold unchanged (false) => no write' if ok else 'UNEXPECTED — see above'}")


asyncio.run(main())
