async def main() -> None:
    payload = "Robert'); DROP TABLE support_grant;--"
    expected_keys = {
        "id", "org_id", "requested_by_admin_id", "requested_by_email", "reason", "status",
        "is_active", "decided_at", "decided_by_email", "expires_at", "created_at",
    }
    async with _client() as c:
        plat, _ = await platform_login_pair(c)
        a = await provision_company(c, plat, "aea35")
        oid = a["org_id"]
        print(f"1) provisioned A={oid}")

        r = await request_support(c, plat, oid, reason=payload)
        body = r.json()
        gid = body["id"]
        round_trips = body["reason"] == payload
        print(f'2) POST request reason="Robert\'); DROP TABLE support_grant;--": {r.status_code}')
        print(f"   returned reason == input?  -> {round_trips}")

        keys_ok = set(body.keys()) == expected_keys
        print(f"3) response keys: {sorted(body.keys())}")
        print(f"   == SupportGrantResponse metadata set -> {keys_ok} (no content leakage)")

        # The table must still be usable: a fresh request must succeed.
        r2 = await request_support(c, plat, oid, reason="follow-up after injection")
        print(f"4) follow-up request on same table: {r2.status_code} (table usable)")

        print(f"GRANT_ID={gid}")
        ok = r.status_code == 201 and round_trips and keys_ok and r2.status_code == 201
        print("HARNESS-OK injection accepted+round-tripped+content-blind; psql verifies table+literal"
              if ok else "FAIL injection handling")


asyncio.run(main())
