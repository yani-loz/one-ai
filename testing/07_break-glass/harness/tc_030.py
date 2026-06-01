async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "aea30")
        print(f"1) provisioned A={a['org_id']} (company_admin token issued)")

        # Direction 1: a REAL company_admin token on the platform request endpoint.
        d1 = await c.post(
            f"/platform/orgs/{a['org_id']}/support-requests",
            headers=bearer(a["admin_access"]),
            json={"reason": "company token probing platform endpoint"},
        )
        print(f"2) company_token -> POST /platform/.../support-requests : {d1.status_code} body={d1.text}")

        # A genuine, approvable grant so the platform-token approve hits real state (not a 404).
        req = await request_support(c, plat, a["org_id"], reason="audience-confinement target")
        gid = req.json()["id"]
        print(f"3) platform requested grant on A: {req.status_code} status={req.json().get('status')} grant={gid}")

        # Direction 2: a REAL platform token on the company approve endpoint.
        d2 = await c.post(f"/support-access/{gid}/approve", headers=bearer(plat))
        print(f"4) platform_token -> POST /support-access/{{gid}}/approve : {d2.status_code} body={d2.text}")

        # The grant must remain `requested` (the audience gate fired before any state change).
        mine = await list_my_requests(c, plat)
        g = next((x for x in mine.json() if x["id"] == gid), None)
        untouched = g is not None and g["status"] == GRANT_REQUESTED and g["is_active"] is False
        print(f"5) grant still requested after rejected approve? status={g['status'] if g else None} "
              f"is_active={g['is_active'] if g else None} -> {untouched}")

        ok = d1.status_code == 401 and d2.status_code == 401 and untouched
        print("PASS both directions 401; grant untouched" if ok else "FAIL audience confinement breached")


asyncio.run(main())
