async def main():
    async with _client() as c:
        plat = await platform_login(c)

        # Real demo platform admin id — the sub we forge into a COMPANY-aud token.
        me = await c.get("/platform/me", headers=bearer(plat))
        me.raise_for_status()
        real_admin_id = me.json()["id"]
        print("REAL_PLATFORM_ADMIN_ID", real_admin_id)

        # Fresh AUTHZ org under test — must stay untouched.
        org = await provision_company(c, plat, "authz-aud")
        print("ORG", org["slug"], org["org_id"])

        # Forge a company-aud token: VALID signature (dev secret) + VALID exp,
        # carrying the real platform admin's sub. Only the audience is "wrong".
        forged_company = forge_company_token(
            sub=real_admin_id, org_id=org["org_id"], role="platform_admin"
        )

        erase = await erase_org(c, forged_company, org["org_id"], confirm_slug=org["slug"])
        print("ERASE_STATUS", erase.status_code)
        print("ERASE_BODY", erase.text)

        export = await compliance_export(c, forged_company, org["org_id"])
        print("EXPORT_STATUS", export.status_code)
        print("EXPORT_BODY", export.text)

        # psql proof comes from a separate db-container query; echo identifiers for it.
        print("PSQL_TARGET_SLUG", org["slug"])
        print("PSQL_TARGET_ORG_ID", org["org_id"])

asyncio.run(main())
