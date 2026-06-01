

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        a = await provision_company(c, plat, "iso-012-a")
        b = await provision_company(c, plat, "iso-012-b")
        print(f"orgA={a['org_id']} slug={a['slug']}")
        print(f"orgB={b['org_id']} slug={b['slug']}")

        r = await request_support(c, plat, a["org_id"])
        gid = r.json()["id"]
        print(f"request -> grant_id={gid} status={r.json()['status']}")

        cdeny = await company_deny(c, b["admin_access"], gid)
        print(f"B-admin deny A's grant (requested) -> status={cdeny.status_code} body={cdeny.json()}")

        appr = await company_approve(c, a["admin_access"], gid)
        ab = appr.json()
        print(f"A-admin approve G -> status={appr.status_code} status={ab['status']} is_active={ab['is_active']}")

        crev = await company_revoke(c, b["admin_access"], gid)
        print(f"B-admin revoke A's APPROVED grant -> status={crev.status_code}  (expect 404, NOT 409/200) body={crev.json()}")

        print(f"GRANT_ID={gid}")

        own = await company_revoke(c, a["admin_access"], gid)
        ob = own.json()
        print(f"A-admin revoke G (positive control) -> status={own.status_code} status={ob['status']} is_active={ob['is_active']}")

        verdict = (cdeny.status_code == 404 and appr.status_code == 200
                   and crev.status_code == 404 and own.status_code == 200
                   and ob["status"] == "revoked")
        print(f"ASSERT cross_deny==404 AND cross_revoke==404 AND own_revoke==200/revoked -> {'PASS' if verdict else 'FAIL'}")

asyncio.run(main())
