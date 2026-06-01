# TC-PC-003 — Refresh single-use (serial): rotate once (200), re-present the OLD refresh -> 401.
# Run: cat _common.py tc_003.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        _old_access, old_refresh = await platform_login_pair(c)

        first = await c.post("/platform/refresh", json={"refresh_token": old_refresh})
        print("FIRST-ROTATION STATUS:", first.status_code, "BODY:", first.json())

        reuse = await c.post("/platform/refresh", json={"refresh_token": old_refresh})
        print("REUSE-OLD STATUS:", reuse.status_code, "BODY:", reuse.json())

        print("SINGLE-USE-HELD:", first.status_code == 200 and reuse.status_code == 401)


asyncio.run(main())
