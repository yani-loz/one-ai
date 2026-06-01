async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract40")
        oid = comp["org_id"]

        # NO Authorization header on each new endpoint. Send VALID bodies so a 401 can
        # only come from missing auth, not body validation (isolate the variable).
        g = await c.get(f"/platform/orgs/{oid}")
        print(f"GET detail (no bearer) -> {g.status_code} (expect 401) body={g.text}")

        s = await c.patch(f"/platform/orgs/{oid}/status", json={"status": "active"})
        print(f"PATCH status (no bearer) -> {s.status_code} (expect 401) body={s.text}")

        lh = await c.patch(f"/platform/orgs/{oid}/legal-hold", json={"legal_hold": False})
        print(f"PATCH legal-hold (no bearer) -> {lh.status_code} (expect 401) body={lh.text}")


asyncio.run(main())
