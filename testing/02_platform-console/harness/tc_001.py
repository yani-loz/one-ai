# TC-PC-001 — GET /platform/me returns the demo admin's real identity (exactly {id,email,full_name}).
# Run: cat _common.py tc_001.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        access, _refresh = await platform_login_pair(c)

        me = await c.get("/platform/me", headers=bearer(access))

        body = me.json()
        keys = sorted(body.keys())
        print("STATUS:", me.status_code)
        print("BODY  :", body)
        print("KEYS  :", keys)
        print("EXACT-3-FIELDS:", keys == ["email", "full_name", "id"])
        print("NO-PASSWORD-HASH:", "password_hash" not in body)
        print("EMAIL-IS-DEMO-ADMIN:", body.get("email") == PLATFORM_EMAIL)


asyncio.run(main())
