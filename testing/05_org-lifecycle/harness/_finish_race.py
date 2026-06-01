# Lead finisher for the RACE suite (phase 2 never ran). Quiet pool assumed.
#   cat testing/05_org-lifecycle/harness/_common.py \
#       testing/05_org-lifecycle/harness/_finish_race.py | docker compose exec -T backend python -


async def main() -> None:
    async with _client() as c:
        plat, _ = await platform_login_pair(c)

        # 060 — suspend-vs-login window: fire 60 concurrent logins with the suspend mid-batch.
        comp = await provision_company(c, plat, "race060")
        oid, email = comp["org_id"], comp["admin_email"]

        async def task060(i):
            if i == 25:
                r = await patch_status(c, plat, oid, STATUS_SUSPENDED)
                return ("SUSPEND", r.status_code, None)
            r = await login(c, email, DEFAULT_PW)
            tok = r.json().get("refresh_token") if r.status_code == 200 else None
            return ("LOGIN", r.status_code, tok)

        res = await fire_concurrent(task060, 60)
        logins = [r for r in res if not isinstance(r, BaseException) and r[0] == "LOGIN"]
        won = [r for r in logins if r[1] == 200]
        blocked = [r for r in logins if r[1] == 403]
        other = [r for r in logins if r[1] not in (200, 403)]
        fresh = await login(c, email, DEFAULT_PW)                       # after the batch settles
        won_refresh = None
        if won:
            rr = await c.post("/auth/refresh", json={"refresh_token": won[0][2]})
            won_refresh = rr.status_code
        print(f"[060] logins 200(won)={len(won)} 403(blocked)={len(blocked)} other={len(other)}; "
              f"FRESH login after batch={fresh.status_code} (expect 403 — no token after commit); "
              f"race-won token refresh={won_refresh} (expect 403 — window-token bounded, cannot refresh)")
        await patch_status(c, plat, oid, STATUS_ACTIVE)

        # 061 — refresh-after-suspend: 50 distinct pre-suspension tokens all blocked under suspension.
        comp = await provision_company(c, plat, "race061")
        oid, email = comp["org_id"], comp["admin_email"]

        async def login_once(i):
            r = await login(c, email, DEFAULT_PW)
            return r.json()["refresh_token"]

        tokens = await fire_concurrent(login_once, 50)
        tokens = [t for t in tokens if isinstance(t, str)]
        await patch_status(c, plat, oid, STATUS_SUSPENDED)

        async def refresh_one(i):
            return await c.post("/auth/refresh", json={"refresh_token": tokens[i]})

        rres = await fire_concurrent(refresh_one, len(tokens))
        print(f"[061] {len(tokens)} pre-susp tokens refreshed under suspension: {summarize(rres)} (expect all 403, none slip a token)")
        await patch_status(c, plat, oid, STATUS_ACTIVE)

        # 062 — concurrent status PATCH integrity (same-row): no 500, final is a valid enum.
        comp = await provision_company(c, plat, "race062")
        oid = comp["org_id"]

        async def flip(i):
            return await patch_status(c, plat, oid, STATUS_SUSPENDED if i % 2 else STATUS_ACTIVE)

        fres = await fire_concurrent(flip, 40)
        final = await get_org_detail(c, plat, oid)
        valid = {"active", "suspended", "onboarding", "offboarded"}
        fstatus = final.json().get("status")
        print(f"[062] 40 concurrent PATCH status alternating: {summarize(fres)}; "
              f"final status={fstatus} valid_enum={fstatus in valid} (expect no 500, valid enum)")
        await patch_status(c, plat, oid, STATUS_ACTIVE)


asyncio.run(main())
