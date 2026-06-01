async def main() -> None:
    prefix = "onb43"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()
        slug_a = f"{prefix}-{stmp}-a"
        slug_b = f"{prefix}-{stmp}-b"  # the fresh unique slug Sb that must NOT survive
        email = f"{prefix}-{stmp}@oneai.dev"

        r1 = await onboard_org(c, plat, name=f"Org {slug_a}", slug=slug_a, admin_email=email)
        print("onboard A (email E):", r1.status_code)

        # Org B: fresh unique slug Sb, REUSING email E -> 409 on the SECOND insert
        # (org B is inserted first, then the user insert fails on email UNIQUE -> rollback).
        r2 = await onboard_org(c, plat, name=f"Org {slug_b}", slug=slug_b, admin_email=email)
        print("onboard B (fresh slug Sb, reused email E):", r2.status_code, "->", r2.json())

        # Echo the slugs so the psql ground-truth step targets the exact value.
        print("SLUG_A:", slug_a)
        print("SLUG_B (must NOT exist in DB):", slug_b)


asyncio.run(main())
