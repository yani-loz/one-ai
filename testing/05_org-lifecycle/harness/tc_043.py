async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract43")
        oid = comp["org_id"]

        # Re-confirm the missing-bearer gate is present on ALL THREE new routes.
        # Valid bodies so any 401 is purely the gate, not body validation.
        results = {}
        g = await c.get(f"/platform/orgs/{oid}")
        results["GET detail"] = g.status_code
        s = await c.patch(f"/platform/orgs/{oid}/status", json={"status": "active"})
        results["PATCH status"] = s.status_code
        lh = await c.patch(f"/platform/orgs/{oid}/legal-hold", json={"legal_hold": False})
        results["PATCH legal-hold"] = lh.status_code

        for route, code in results.items():
            print(f"{route} (no bearer) -> {code} (expect 401)")
        print(f"all_three_401={all(v == 401 for v in results.values())}")


asyncio.run(main())
