async def main() -> None:
    print("== TC-OL-023 — REAL company_admin token rejected on all three lifecycle endpoints ==")
    async with _client() as c:
        plat = await platform_login(c)
        comp = await provision_company(c, plat, "xdom")
        oid = comp["org_id"]
        ctoken = comp["admin_access"]
        before = await get_org_detail(c, plat, oid)
        print(f"[setup]   own org: {comp['slug']} ({oid}) "
              f"status={before.json()['status']} legal_hold={before.json()['legal_hold']}")
        print(f"          real company_admin access token issued for this org")

        g = await get_org_detail(c, ctoken, oid)
        print(f"[attack1] GET /platform/orgs/{{own id}} (company token): {g.status_code} body={g.json()}")

        s = await patch_status(c, ctoken, oid, STATUS_SUSPENDED)
        print(f"[attack2] PATCH /status {{suspended}} (company token): {s.status_code} body={s.json()}")

        lh = await patch_legal_hold(c, ctoken, oid, True)
        print(f"[attack3] PATCH /legal-hold {{true}} (company token): {lh.status_code} body={lh.json()}")

        after = await get_org_detail(c, plat, oid)
        status_after = after.json()["status"]
        lh_after = after.json()["legal_hold"]
        print(f"[readback] GET detail (real platform token): {after.status_code} "
              f"status={status_after} legal_hold={lh_after}")

        all_401 = g.status_code == 401 and s.status_code == 401 and lh.status_code == 401
        unchanged = status_after == STATUS_ACTIVE and lh_after is False
        ok = all_401 and unchanged
        print(f"RESULT: {'PASS' if ok else 'FAIL'} — "
              f"{'company side cannot reach platform lifecycle (all 401); org unchanged' if ok else 'UNEXPECTED — see above'}")


asyncio.run(main())
