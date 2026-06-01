async def main() -> None:
    prefix = "onb41"
    async with _client() as c:
        plat = await platform_login(c)
        slug = f"{prefix}-{stamp()}"
        email1 = f"{slug}-a@oneai.dev"
        email2 = f"{slug}-b@oneai.dev"

        r1 = await onboard_org(c, plat, name=f"Org {slug}", slug=slug, admin_email=email1)
        print("first onboard (slug S):", r1.status_code, "->", r1.json())

        # Same slug, DIFFERENT email -> must be 409 (duplicate slug).
        r2 = await onboard_org(c, plat, name=f"Org {slug} dup", slug=slug, admin_email=email2)
        print("second onboard (same slug, diff email):", r2.status_code, "->", r2.json())


asyncio.run(main())
