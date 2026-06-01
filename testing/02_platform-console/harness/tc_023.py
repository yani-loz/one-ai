async def main() -> None:
    print("== TC-PC-023 — real COMPANY admin token rejected on /platform/orgs (GET + POST) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)
        comp = await provision_company(c, p_access, "xdom")
        company_access = comp["admin_access"]
        print("[setup]   provisioned company:", comp["slug"], "admin", comp["admin_email"])

        # GET /platform/orgs with a company token must be 401 (audience).
        listing = await c.get("/platform/orgs", headers=bearer(company_access))
        print("[attack1] GET /platform/orgs (company admin token):", listing.status_code)
        print("          body:", listing.json())

        # POST /platform/orgs (onboard) with a company token must be 401 (audience), BEFORE body.
        slug = f"xdom-shouldnotexist-{stamp()}"
        onboard = await c.post(
            "/platform/orgs",
            headers=bearer(company_access),
            json={
                "org_name": f"Org {slug}",
                "org_slug": slug,
                "admin_email": f"admin-{slug}@oneai.dev",
                "admin_full_name": "Should Not Exist",
                "admin_password": DEFAULT_PW,
            },
        )
        print("[attack2] POST /platform/orgs (company admin token):", onboard.status_code)
        print("          body:", onboard.json())

        ok = listing.status_code == 401 and onboard.status_code == 401
        print("RESULT:", "PASS — company side cannot reach platform endpoints (both 401)"
              if ok else f"FAIL — GET={listing.status_code} POST={onboard.status_code} (expected 401/401)")


asyncio.run(main())
