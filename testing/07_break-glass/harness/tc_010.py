

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        a = await provision_company(c, plat, "iso-010-a")
        b = await provision_company(c, plat, "iso-010-b")
        print(f"orgA={a['org_id']} slug={a['slug']}")
        print(f"orgB={b['org_id']} slug={b['slug']}")

        r = await request_support(c, plat, a["org_id"])
        body = r.json()
        gid = body["id"]
        print(f"request status={r.status_code} grant_id={gid} status={body['status']} org_id={body['org_id']}")

        ia = await company_inbox(c, a["admin_access"])
        rows_a = ia.json()
        present_a = any(row["id"] == gid for row in rows_a)
        row_org_ok = any(row["id"] == gid and row["org_id"] == a["org_id"] for row in rows_a)
        print(f"A inbox status={ia.status_code} grant_in_A_inbox={present_a} A_inbox_row_org_id_matches_A={row_org_ok} A_count={len(rows_a)}")

        ib = await company_inbox(c, b["admin_access"])
        rows_b = ib.json()
        present_b = any(row["id"] == gid for row in rows_b)
        print(f"B inbox status={ib.status_code} grant_in_B_inbox={present_b} B_count={len(rows_b)}")

        verdict = (r.status_code == 201 and body["status"] == "requested"
                   and body["org_id"] == a["org_id"]
                   and ia.status_code == 200 and present_a and row_org_ok
                   and ib.status_code == 200 and not present_b)
        print(f"ASSERT present_in_A={present_a} absent_from_B={not present_b} -> {'PASS' if verdict else 'FAIL'}")

asyncio.run(main())
