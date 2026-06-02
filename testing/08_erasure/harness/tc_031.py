async def main():
    async with _client() as c:
        plat = await platform_login(c)

        # Fresh AUTHZ org; admin_access is a REAL, valid company_admin token for it.
        org = await provision_company(c, plat, "authz-self")
        company_token = org["admin_access"]
        print("ORG", org["slug"], org["org_id"])

        # Tenant tries to self-erase its OWN org via the platform endpoint.
        erase = await erase_org(c, company_token, org["org_id"], confirm_slug=org["slug"])
        print("ERASE_STATUS", erase.status_code)
        print("ERASE_BODY", erase.text)

        # Tenant tries to self-export its OWN org's compliance bundle.
        export = await compliance_export(c, company_token, org["org_id"])
        print("EXPORT_STATUS", export.status_code)
        print("EXPORT_BODY", export.text)

        print("PSQL_TARGET_SLUG", org["slug"])
        print("PSQL_TARGET_ORG_ID", org["org_id"])

asyncio.run(main())
