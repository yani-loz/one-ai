async def main() -> None:
    prefix = "onb42"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()
        slug1 = f"{prefix}-{stmp}-a"
        slug2 = f"{prefix}-{stmp}-b"
        email = f"{prefix}-{stmp}@oneai.dev"

        r1 = await onboard_org(c, plat, name=f"Org {slug1}", slug=slug1, admin_email=email)
        print("first onboard (email E):", r1.status_code, "->", r1.json())

        # NEW slug, SAME email -> must be 409 (duplicate admin email, globally unique).
        r2 = await onboard_org(c, plat, name=f"Org {slug2}", slug=slug2, admin_email=email)
        print("second onboard (new slug, same email):", r2.status_code, "->", r2.json())


asyncio.run(main())
