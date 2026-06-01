async def main() -> None:
    prefix = "onb48"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()

        slug1 = f"{prefix}-{stmp}-a"
        slug2 = f"{prefix}-{stmp}-b"
        # Mixed-case email; canonical local-part stem is unique per run.
        local = f"{prefix}.{stmp}"
        mixed = f"Mixed.Case.{local}@ONEAI.dev"
        lower = mixed.lower()
        print("mixed email:", mixed)
        print("lower email:", lower)

        # 1) Onboard with the MIXED-CASE email -> 201.
        r1 = await onboard_org(c, plat, name=f"Org {slug1}", slug=slug1, admin_email=mixed)
        print("[onboard mixed-case email] status:", r1.status_code)
        print("   returned admin email:", r1.json().get("admin", {}).get("email"))

        # 2) Onboard a NEW slug with the LOWERCASE variant -> 409 (case-variant duplicate).
        r2 = await onboard_org(c, plat, name=f"Org {slug2}", slug=slug2, admin_email=lower)
        print("[onboard lowercase variant, new slug] status:", r2.status_code,
              "(expect 409)")
        print("   detail:", r2.json().get("detail"))

        print("LOWER_EMAIL:", lower)


asyncio.run(main())
