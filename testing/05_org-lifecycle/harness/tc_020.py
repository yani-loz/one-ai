async def main() -> None:
    print("== TC-OL-020 — company-aud token w/ REAL admin sub -> GET /platform/orgs/{id} (DISCRIMINATING) ==")
    async with _client() as c:
        plat = await platform_login(c)

        me = await c.get("/platform/me", headers=bearer(plat))
        real_admin_id = me.json()["id"]
        print(f"[control] GET /platform/me (real platform token): {me.status_code}")
        print(f"          real admin id: {real_admin_id}")

        comp = await provision_company(c, plat, "xdom")
        oid = comp["org_id"]
        ctrl = await get_org_detail(c, plat, oid)
        print(f"[setup]   provisioned target org: {comp['slug']} ({oid})")
        print(f"[control] GET /platform/orgs/{{id}} (real platform token): {ctrl.status_code} "
              f"fields={sorted(ctrl.json().keys()) if ctrl.status_code == 200 else ctrl.text}")

        forged = forge_company_token(sub=real_admin_id, org_id=None)
        attack = await get_org_detail(c, forged, oid)
        print(f"[attack]  GET /platform/orgs/{{id}} (FORGED company-aud token, sub=real admin id): {attack.status_code}")
        print(f"          body: {attack.json()}")

        ok = attack.status_code == 401 and ctrl.status_code == 200
        print(f"RESULT: {'PASS' if ok else 'FAIL'} — "
              f"{'audience guard is load-bearing (401 is audience, not not-found/role)' if ok else 'UNEXPECTED — see codes above'}")


asyncio.run(main())
