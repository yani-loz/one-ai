async def _erase_with_pw(c, plat_token, org_id, *, confirm_slug, password,
                         reason="GDPR offboarding (test)"):
    # LIVE ErasureRequest requires a re-auth `password`. Include it so the request VALIDATES
    # (422 otherwise) and the existence check (404 path) is actually reached.
    return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                        json={"reason": reason, "confirm_slug": confirm_slug, "password": password})


async def main():
    async with _client() as c:
        plat = await platform_login(c)
        print("=== TC-ER-023 ===")

        # Two random uuids that are NOT real orgs. NOTHING real is touched.
        ghost_erase = str(uuid4())
        ghost_export = str(uuid4())
        print("GHOST_ERASE_ID:", ghost_erase)
        print("GHOST_EXPORT_ID:", ghost_export)

        # 1. Erase an unknown org → 404 (get_for_update -> None, before slug check).
        er = await _erase_with_pw(c, plat, ghost_erase, confirm_slug="does-not-matter",
                                  password=PLATFORM_PW)
        print("ERASE_UNKNOWN:", er.status_code, "body:", er.json())

        # 2. Export an unknown org → 404.
        ex = await compliance_export(c, plat, ghost_export)
        print("EXPORT_UNKNOWN:", ex.status_code, "body:", ex.json())

        ok = er.status_code == 404 and ex.status_code == 404
        print("RESULT:", "PASS" if ok else "FAIL")


asyncio.run(main())
