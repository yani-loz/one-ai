# TC-PC-008 — Unknown admin: forge a valid-signature, aud='platform' token with a RANDOM sub
# (no platform_admins row) -> GET /platform/me -> 401 (build_admin_view_by_id finds no row).
# Run: cat _common.py tc_008.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        ghost_sub = str(uuid4())
        token = forge_platform_token(sub=ghost_sub)
        print("FORGED-SUB:", ghost_sub)

        # Confirm the forged token is structurally a valid platform token (right secret+aud).
        decoded = jwt.decode(
            token, DEV_SECRET, algorithms=[ALG], audience=PLATFORM_AUD,
            options={"require": ["exp", "aud", "sub"]},
        )
        print("DECODE-OK aud=", decoded["aud"], "sub=", decoded["sub"])

        me = await c.get("/platform/me", headers=bearer(token))
        print("GHOST /platform/me STATUS:", me.status_code, "BODY:", me.json())
        print("UNKNOWN-ADMIN-REJECTED:", me.status_code == 401)


asyncio.run(main())
