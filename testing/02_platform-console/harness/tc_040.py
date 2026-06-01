async def main() -> None:
    prefix = "onb40"
    async with _client() as c:
        plat = await platform_login(c)
        slug = f"{prefix}-{stamp()}"
        email = f"{slug}@oneai.dev"
        r = await onboard_org(
            c, plat, name=f"Org {slug}", slug=slug, admin_email=email,
            admin_name="ONB Forty Admin",
        )
        print("onboard status:", r.status_code)
        body = r.json()
        print("body:", body)

        org = body.get("organization", {})
        admin = body.get("admin", {})
        print("org keys (sorted):", sorted(org.keys()))
        print("org user_count:", org.get("user_count"))
        print("org created_at present:", "created_at" in org and org["created_at"] is not None)
        print("admin keys (sorted):", sorted(admin.keys()))
        print("admin role:", admin.get("role"))
        print("admin password_hash present:", "password_hash" in admin)
        print("admin is_active:", admin.get("is_active"))
        print("admin org_id == org id:", admin.get("org_id") == org.get("id"))


asyncio.run(main())
