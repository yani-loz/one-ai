# TC-PC-007 — Token outlives account (tw-lifecycle): tokens captured while ACTIVE must both 401
# after the admin is deactivated via psql. Phase-aware via /tmp/pses007.txt (persists across
# `exec` calls in the long-running backend container; /tmp is NOT under backend/ so no reload).
#   Phase 1 (no temp file): login tw-lifecycle while active, assert /platform/me=200, persist tokens.
#   Phase 2 (temp file present, after psql is_active=false): same access -> /platform/me -> 401
#                                                            AND same refresh -> /platform/refresh -> 401.
# Run: cat _common.py tc_007.py | docker compose exec -T backend python -
_STATE = "/tmp/pses007.txt"
_LIFECYCLE_EMAIL = "tw-lifecycle-tw06012c3@oneai.dev"


async def main() -> None:
    async with _client() as c:
        try:
            fh = open(_STATE)
            saved = fh.read()
            fh.close()
            phase = 2
        except FileNotFoundError:
            phase = 1

        if phase == 1:
            r = await c.post(
                "/platform/login",
                json={"email": _LIFECYCLE_EMAIL, "password": DEFAULT_PW},
            )
            print("PHASE-1 LOGIN STATUS:", r.status_code)
            r.raise_for_status()
            body = r.json()
            access = body["access_token"]
            refresh = body["refresh_token"]

            me = await c.get("/platform/me", headers=bearer(access))
            print("PHASE-1 /platform/me (ACTIVE) STATUS:", me.status_code, "BODY:", me.json())

            out = open(_STATE, "w")
            out.write(access + "\n" + refresh)
            out.close()
            print("PHASE-1 TOKENS PERSISTED:", me.status_code == 200)
        else:
            access, refresh = saved.split("\n", 1)

            me = await c.get("/platform/me", headers=bearer(access))
            print("PHASE-2 /platform/me (DEACTIVATED) STATUS:", me.status_code, "BODY:", me.json())

            rf = await c.post("/platform/refresh", json={"refresh_token": refresh})
            print("PHASE-2 /platform/refresh (DEACTIVATED) STATUS:", rf.status_code, "BODY:", rf.json())

            print(
                "TOKEN-DIES-WITH-ACCOUNT:",
                me.status_code == 401 and rf.status_code == 401,
            )


asyncio.run(main())
