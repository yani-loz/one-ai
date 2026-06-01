async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract34")
        oid = comp["org_id"]

        # All four valid values -> 200, new status echoed. FINISH ON 'active'.
        for value in [STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_ONBOARDING,
                      STATUS_OFFBOARDED, STATUS_ACTIVE]:
            r = await patch_status(c, p_access, oid, value)
            echoed = r.json().get("status") if r.status_code == 200 else r.text
            print(f"PATCH status={value} -> {r.status_code} echoed_status={echoed}")

        # Confirm the org ended on 'active'.
        det = await get_org_detail(c, p_access, oid)
        print(f"final read-back status={det.json().get('status')} (must be 'active')")


asyncio.run(main())
