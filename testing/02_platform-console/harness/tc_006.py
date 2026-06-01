# TC-PC-006 — Rotated-then-logout chain: rotate (old->401), logout the NEW token, NEW no longer refreshes (->401).
# Run: cat _common.py tc_006.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        _old_access, old_refresh = await platform_login_pair(c)

        rot = await c.post("/platform/refresh", json={"refresh_token": old_refresh})
        new_refresh = rot.json().get("refresh_token")
        print("ROTATE STATUS:", rot.status_code)

        reuse_old = await c.post("/platform/refresh", json={"refresh_token": old_refresh})
        print("OLD-REUSE STATUS:", reuse_old.status_code, "BODY:", reuse_old.json())

        out = await c.post("/platform/logout", json={"refresh_token": new_refresh})
        print("LOGOUT-NEW STATUS:", out.status_code, "BODY:", repr(out.text))

        new_after = await c.post("/platform/refresh", json={"refresh_token": new_refresh})
        print("NEW-REFRESH-AFTER-LOGOUT STATUS:", new_after.status_code, "BODY:", new_after.json())

        composed = (
            rot.status_code == 200
            and reuse_old.status_code == 401
            and out.status_code == 204
            and new_after.status_code == 401
        )
        print("ROTATION+LOGOUT-COMPOSE:", composed)


asyncio.run(main())
