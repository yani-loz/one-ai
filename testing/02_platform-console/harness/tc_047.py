async def main() -> None:
    prefix = "onb47"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()

        # 1) org_name with a NUL byte -> must be 422 (SafeName/DYN-03), NOT 500.
        slug_nul = f"{prefix}-{stmp}-nul"
        nul_name = "ev" + chr(0) + "il"  # real U+0000 control char inside the name
        print("nul_name contains real NUL:", "\x00" in nul_name, "len:", len(nul_name))
        r1 = await onboard_org(
            c, plat, name=nul_name, slug=slug_nul,
            admin_email=f"{slug_nul}@oneai.dev",
        )
        print("[NUL byte in org_name] status:", r1.status_code, "(expect 422, NOT 500)")
        print("   detail:", r1.json().get("detail"))

        # 2) SQL-injection org_name -> stored LITERALLY -> 201; tables intact.
        slug_inj = f"{prefix}-{stmp}-inj"
        injection = "Robert'); DROP TABLE users;--"
        r2 = await onboard_org(
            c, plat, name=injection, slug=slug_inj,
            admin_email=f"{slug_inj}@oneai.dev",
        )
        print("[SQL-injection org_name] status:", r2.status_code, "(expect 201)")
        stored_name = r2.json().get("organization", {}).get("name")
        print("   returned name == input literally:", stored_name == injection)
        print("   returned name:", repr(stored_name))
        print("SLUG_INJ:", slug_inj)


asyncio.run(main())
