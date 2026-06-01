# TC-PC-002 — POST /platform/refresh rotates to a brand-new pair; both tokens differ from originals.
# Run: cat _common.py tc_002.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        old_access, old_refresh = await platform_login_pair(c)

        r = await c.post("/platform/refresh", json={"refresh_token": old_refresh})

        body = r.json()
        keys = sorted(body.keys())
        new_access = body.get("access_token")
        new_refresh = body.get("refresh_token")
        print("STATUS:", r.status_code)
        print("KEYS  :", keys)
        print("EXACT-3-FIELDS:", keys == ["access_token", "refresh_token", "token_type"])
        print("TOKEN-TYPE:", body.get("token_type"))
        print("NO-USER-FIELD:", "user" not in body)
        print("ACCESS-DIFFERS:", bool(new_access) and new_access != old_access)
        print("REFRESH-DIFFERS:", bool(new_refresh) and new_refresh != old_refresh)


asyncio.run(main())
