

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        a = await provision_company(c, plat, "iso-011-a")
        b = await provision_company(c, plat, "iso-011-b")
        print(f"orgA={a['org_id']} slug={a['slug']}")
        print(f"orgB={b['org_id']} slug={b['slug']}")

        r = await request_support(c, plat, a["org_id"])
        gid = r.json()["id"]
        print(f"request status={r.status_code} grant_id={gid} status={r.json()['status']}")

        cross = await company_approve(c, b["admin_access"], gid)
        print(f"B-admin approve A's grant -> status={cross.status_code} body={cross.json()}")

        # Existence-oracle check: a truly-nonexistent grant_id (as B-admin) must be
        # byte-indistinguishable from the cross-org case (same status + same body).
        import uuid as _uuid
        ghost = str(_uuid.uuid4())
        nf = await company_approve(c, b["admin_access"], ghost)
        print(f"B-admin approve NONEXISTENT grant -> status={nf.status_code} body={nf.json()}")
        oracle_safe = (nf.status_code == cross.status_code and nf.json() == cross.json())
        print(f"existence_oracle_safe (cross-org == truly-absent)={oracle_safe}")

        ia = await company_inbox(c, a["admin_access"])
        row = next((x for x in ia.json() if x["id"] == gid), None)
        print(f"A inbox read-back: status={row['status']} decided_at={row['decided_at']} decided_by_email={row['decided_by_email']}")

        untouched = (row["status"] == "requested" and row["decided_at"] is None
                     and row["decided_by_email"] is None)
        verdict = cross.status_code == 404 and untouched and oracle_safe
        print(f"GRANT_ID={gid}")
        print(f"ASSERT cross_approve==404 AND untouched AND oracle_safe -> {'PASS' if verdict else 'FAIL'}")

asyncio.run(main())
