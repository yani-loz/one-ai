async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract30")
        oid = comp["org_id"]

        det = await get_org_detail(c, p_access, oid)
        body = det.json()
        expected = {"id", "name", "slug", "status", "user_count", "legal_hold", "created_at"}
        actual = set(body.keys())
        forbidden = {"password_hash", "hash", "content", "cost", "costs", "token", "tokens",
                     "admin_email", "email", "admin", "users"}
        leaked = actual & forbidden
        print(f"GET detail status={det.status_code}")
        print(f"fields={sorted(actual)}")
        print(f"expected_exactly={sorted(expected)}")
        print(f"matches_exactly={actual == expected}")
        print(f"leaked_forbidden_fields={sorted(leaked)}")
        print(f"raw_body={body}")


asyncio.run(main())
