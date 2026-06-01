

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        a = await provision_company(c, plat, "iso-013-a")
        print(f"orgA={a['org_id']} slug={a['slug']}")

        import uuid as _uuid
        fsub = str(_uuid.uuid4())
        ftok = forge_platform_token(sub=fsub)
        print(f"forged sub={fsub}")

        r = await request_support(c, ftok, a["org_id"])
        body = r.json()
        gid = body["id"]
        print(f"request as F -> status={r.status_code} grant_id={gid} "
              f"requested_by_admin_id={body['requested_by_admin_id']} requested_by_email={body['requested_by_email']}")

        flist = await list_my_requests(c, ftok)
        present_f = any(x["id"] == gid for x in flist.json())
        print(f"F list -> status={flist.status_code} grant_in_F_list={present_f} F_count={len(flist.json())}")

        dlist = await list_my_requests(c, plat)
        present_d = any(x["id"] == gid for x in dlist.json())
        print(f"demo-admin list -> status={dlist.status_code} grant_in_demo_list={present_d} demo_count={len(dlist.json())}")

        verdict = (r.status_code == 201 and body["requested_by_admin_id"] == fsub
                   and body["requested_by_email"] is None
                   and flist.status_code == 200 and present_f
                   and dlist.status_code == 200 and not present_d)
        print(f"ASSERT present_for_F={present_f} AND absent_for_demo={not present_d} -> {'PASS' if verdict else 'FAIL'}")

asyncio.run(main())
