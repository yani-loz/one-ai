

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        a = await provision_company(c, plat, "iso-014-a")
        print(f"orgA={a['org_id']} slug={a['slug']}")

        import uuid as _uuid
        fsub = str(_uuid.uuid4())
        ftok = forge_platform_token(sub=fsub)
        print(f"forged sub={fsub}")

        r = await request_support(c, ftok, a["org_id"])
        body = r.json()
        gid = body["id"]
        print(f"request as F -> status={r.status_code} grant_id={gid} status={body['status']} "
              f"requested_by_admin_id={body['requested_by_admin_id']}")

        demo_rev = await platform_revoke_request(c, plat, gid)
        print(f"demo-admin revoke F's grant -> status={demo_rev.status_code} body={demo_rev.json()}")
        print(f"GRANT_ID={gid}")

        f_rev = await platform_revoke_request(c, ftok, gid)
        fb = f_rev.json()
        print(f"F revoke own grant (positive control) -> status={f_rev.status_code} status={fb['status']} is_active={fb['is_active']}")

        verdict = (r.status_code == 201 and demo_rev.status_code == 404
                   and f_rev.status_code == 200 and fb["status"] == "revoked")
        print(f"ASSERT demo_revoke==404 AND F_revoke==200/revoked -> {'PASS' if verdict else 'FAIL'}")

asyncio.run(main())
