async def main():
    import asyncpg  # _common.py provides no DB helper; asyncpg used for atomic ground-truth reads

    db = await asyncpg.connect(host="db", user="oneai", password="oneai", database="oneai", port=5432)

    async def erase_with_pw(c, plat_token, org_id, *, confirm_slug, password, reason="GDPR offboarding (test)"):
        return await c.post(f"/platform/orgs/{org_id}/erase", headers=bearer(plat_token),
                            json={"reason": reason, "confirm_slug": confirm_slug, "password": password})

    async def gt(org_id):
        users = await db.fetchval("SELECT count(*) FROM users WHERE org_id=$1", org_id)
        status = await db.fetchval("SELECT status FROM organizations WHERE id=$1", org_id)
        return users, status

    N = 32
    tally = {"hold_wins": 0, "erase_wins": 0, "other_erase_code": 0, "5xx": 0,
             "inconsistent": 0, "exc": 0}
    erase_codes = {}
    hold_codes = {}
    bad = []

    async with _client() as c:
        plat = await platform_login(c)
        for i in range(N):
            org = await provision_company(c, plat, f"hold-er4-{i}")
            org_id = org["org_id"]
            slug = org["slug"]
            oid = __import__("uuid").UUID(org_id)

            # fire erase + set-legal-hold CONCURRENTLY on the SAME fresh org row
            res = await asyncio.gather(
                erase_with_pw(c, plat, org_id, confirm_slug=slug, password=PLATFORM_PW),
                patch_legal_hold(c, plat, org_id, True),
                return_exceptions=True,
            )
            er, hr = res
            if isinstance(er, BaseException) or isinstance(hr, BaseException):
                tally["exc"] += 1
                bad.append(f"i={i} EXC erase={er!r} hold={hr!r}")
                continue
            ec, hc = er.status_code, hr.status_code
            erase_codes[ec] = erase_codes.get(ec, 0) + 1
            hold_codes[hc] = hold_codes.get(hc, 0) + 1

            users, status = await gt(oid)

            if ec >= 500 or hc >= 500:
                tally["5xx"] += 1
                bad.append(f"i={i} 5xx erase={ec} hold={hc}")
                continue

            if ec == 409:
                # hold-wins: org must be fully intact + active
                if users == 1 and status == "active":
                    tally["hold_wins"] += 1
                else:
                    tally["inconsistent"] += 1
                    bad.append(f"i={i} HOLD-WINS-BUT-TORN erase=409 users={users} status={status}")
            elif ec == 200:
                # erase-wins: users gone + offboarded
                if users == 0 and status == "offboarded":
                    tally["erase_wins"] += 1
                else:
                    tally["inconsistent"] += 1
                    bad.append(f"i={i} ERASE-WINS-BUT-TORN erase=200 users={users} status={status}")
            else:
                tally["other_erase_code"] += 1
                bad.append(f"i={i} UNEXPECTED erase={ec} users={users} status={status} body={er.text[:200]}")

    await db.close()

    print("iterations:", N)
    print("erase_status_spread:", erase_codes)
    print("hold_status_spread:", hold_codes)
    print("tally:", tally)
    print("anomalies:", bad if bad else "none")
    ok = (tally["5xx"] == 0 and tally["inconsistent"] == 0 and tally["exc"] == 0
          and tally["other_erase_code"] == 0)
    print("VERDICT:", "PASS" if ok else "FAIL")


asyncio.run(main())
