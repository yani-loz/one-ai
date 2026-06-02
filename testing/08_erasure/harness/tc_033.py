async def main():
    async with _client() as c:
        plat = await platform_login(c)

        # Fresh AUTHZ org (read-only target — export does not mutate).
        org = await provision_company(c, plat, "authz-fexp")
        print("ORG", org["slug"], org["org_id"])

        # Forged platform-aud token: RANDOM sub, dev secret, valid exp.
        forged_platform = forge_platform_token()

        export = await compliance_export(c, forged_platform, org["org_id"])
        print("EXPORT_STATUS", export.status_code)
        body = export.json() if export.status_code == 200 else export.text
        if isinstance(body, dict):
            print("EXPORT_KEYS", sorted(body.keys()))
            print("ORGANIZATION", body.get("organization"))
            audit = body.get("audit", [])
            print("AUDIT_LEN", len(audit))
            print("AUDIT_FIRST", audit[0] if audit else None)
            print("HAS_GENERATED_AT", "generated_at" in body)
        else:
            print("EXPORT_BODY", body)

        print("PSQL_TARGET_SLUG", org["slug"])
        print("PSQL_TARGET_ORG_ID", org["org_id"])

asyncio.run(main())
