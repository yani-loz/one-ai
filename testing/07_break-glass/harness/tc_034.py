async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "aea34")
        oid = a["org_id"]
        url = f"/platform/orgs/{oid}/support-requests"
        print(f"1) provisioned A={oid}")

        # A: empty reason -> min_length=1 violation
        ra = await c.post(url, headers=bearer(plat), json={"reason": ""})
        da = ra.json()["detail"][0]
        print(f'2) reason="" (empty):            {ra.status_code}  type={da["type"]}  loc={da["loc"]}')

        # B: 501 chars -> max_length=500 violation
        rb = await c.post(url, headers=bearer(plat), json={"reason": "x" * 501})
        db = rb.json()["detail"][0]
        print(f"3) reason=<501 chars>:           {rb.status_code}  type={db['type']}  loc={db['loc']}")

        # C: extra field (smuggle status=approved) -> extra='forbid' violation
        rc = await c.post(url, headers=bearer(plat), json={"reason": "valid", "status": "approved"})
        dc = rc.json()["detail"][0]
        print(f"4) {{reason:valid, status:approved}} (extra): {rc.status_code} type={dc['type']} loc={dc['loc']}")

        # No grant on org A should have been created by any of the three.
        mine = await list_my_requests(c, plat)
        on_a = [g for g in mine.json() if g["org_id"] == oid]
        print(f"5) grants on org A created by the above: {len(on_a)}  (expect 0)")

        ok = (ra.status_code == 422 and rb.status_code == 422 and rc.status_code == 422
              and len(on_a) == 0)
        print("PASS all three 422; no grant created; mass-assignment of status blocked"
              if ok else "FAIL input bounds not enforced")


asyncio.run(main())
