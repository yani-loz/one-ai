async def main() -> None:
    print("== TC-PC-022 — real PLATFORM token rejected on COMPANY endpoints (/auth/me, /users) ==")
    async with _client() as c:
        p_access, _ = await platform_login_pair(c)

        me = await c.get("/auth/me", headers=bearer(p_access))
        print("[attack1] GET /auth/me (platform access token):", me.status_code)
        print("          body:", me.json())

        users = await c.get("/users", headers=bearer(p_access))
        print("[attack2] GET /users (platform access token):", users.status_code)
        print("          body:", users.json())

        ok = me.status_code == 401 and users.status_code == 401
        print("RESULT:", "PASS — both directions confined (platform token cannot reach company endpoints)"
              if ok else f"FAIL — /auth/me={me.status_code} /users={users.status_code} (expected 401/401)")


asyncio.run(main())
