async def main() -> None:
    prefix = "onb46"
    async with _client() as c:
        plat = await platform_login(c)
        stmp = stamp()

        slug1 = f"{prefix}-{stmp}-extra"
        # 1) Extra unknown field -> 422 (model_config extra='forbid').
        body_extra = {
            "org_name": "Org Extra",
            "org_slug": slug1,
            "admin_email": f"{slug1}@oneai.dev",
            "admin_full_name": "Extra Admin",
            "admin_password": "Valid-Pass-2026!",
            "is_superuser": True,  # unknown/forbidden field — privilege-escalation probe
        }
        r1 = await c.post("/platform/orgs", headers=bearer(plat), json=body_extra)
        print("[extra unknown field 'is_superuser'] status:", r1.status_code)
        print("   detail:", r1.json().get("detail"))

        slug2 = f"{prefix}-{stmp}-missing"
        # 2) Missing required admin_password -> 422.
        body_missing = {
            "org_name": "Org Missing",
            "org_slug": slug2,
            "admin_email": f"{slug2}@oneai.dev",
            "admin_full_name": "Missing Admin",
            # admin_password intentionally omitted
        }
        r2 = await c.post("/platform/orgs", headers=bearer(plat), json=body_missing)
        print("[missing admin_password] status:", r2.status_code)
        print("   detail:", r2.json().get("detail"))


asyncio.run(main())
