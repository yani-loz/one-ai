async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract36")
        oid = comp["org_id"]

        # Set legal_hold true, then FRESH GET read-back.
        r = await patch_legal_hold(c, p_access, oid, True)
        print(f"PATCH legal_hold=true -> {r.status_code} legal_hold={r.json().get('legal_hold')}")
        det = await get_org_detail(c, p_access, oid)
        print(f"FRESH GET read-back -> {det.status_code} legal_hold={det.json().get('legal_hold')} (expect true)")

        # Clear it, then FRESH GET read-back.
        r = await patch_legal_hold(c, p_access, oid, False)
        print(f"PATCH legal_hold=false -> {r.status_code} legal_hold={r.json().get('legal_hold')}")
        det = await get_org_detail(c, p_access, oid)
        print(f"FRESH GET read-back -> {det.status_code} legal_hold={det.json().get('legal_hold')} (expect false)")


asyncio.run(main())
