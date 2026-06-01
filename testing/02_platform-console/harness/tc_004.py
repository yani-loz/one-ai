# TC-PC-004 — Logout revokes: login, POST /platform/logout (204), then /platform/refresh -> 401.
# Run: cat _common.py tc_004.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        _access, refresh = await platform_login_pair(c)

        out = await c.post("/platform/logout", json={"refresh_token": refresh})
        print("LOGOUT STATUS:", out.status_code, "BODY:", repr(out.text))

        after = await c.post("/platform/refresh", json={"refresh_token": refresh})
        print("REFRESH-AFTER-LOGOUT STATUS:", after.status_code, "BODY:", after.json())

        print("LOGOUT-REVOKES:", out.status_code == 204 and after.status_code == 401)


asyncio.run(main())
