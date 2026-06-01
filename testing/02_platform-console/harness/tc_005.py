# TC-PC-005 — Logout idempotency: logout same token twice -> 204 both; logout unknown random token -> 204.
# Run: cat _common.py tc_005.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        _access, refresh = await platform_login_pair(c)

        out1 = await c.post("/platform/logout", json={"refresh_token": refresh})
        out2 = await c.post("/platform/logout", json={"refresh_token": refresh})
        print("LOGOUT-1 STATUS:", out1.status_code, "BODY:", repr(out1.text))
        print("LOGOUT-2 STATUS:", out2.status_code, "BODY:", repr(out2.text))

        unknown = str(uuid4())
        out3 = await c.post("/platform/logout", json={"refresh_token": unknown})
        print("LOGOUT-UNKNOWN-TOKEN:", unknown)
        print("LOGOUT-UNKNOWN STATUS:", out3.status_code, "BODY:", repr(out3.text))

        all_204 = out1.status_code == 204 and out2.status_code == 204 and out3.status_code == 204
        print("IDEMPOTENT-NO-ENUMERATION:", all_204)


asyncio.run(main())
