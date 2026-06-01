# Harness validation probe — NOT a test case. Concatenated after _common.py and piped into
# the backend container to prove every helper works + preview the discriminating XDOM cases:
#   cat testing/02_platform-console/harness/_common.py \
#       testing/02_platform-console/harness/_probe.py | docker compose exec -T backend python -
# (No `from __future__` here — _common.py provides it as the first statement.)


async def main() -> None:
    async with _client() as c:
        p_access, p_refresh = await platform_login_pair(c)
        print("1) platform_login_pair: access?", bool(p_access), "refresh?", bool(p_refresh))

        me = await c.get("/platform/me", headers=bearer(p_access))
        real_admin_id = me.json().get("id")
        print("2) GET /platform/me (real token):", me.status_code, "->", me.json())

        orgs = await c.get("/platform/orgs", headers=bearer(p_access))
        rows = orgs.json()
        print("3) GET /platform/orgs:", orgs.status_code, "count=", len(rows),
              "fields=", sorted(rows[0].keys()) if rows else "none")

        comp = await provision_company(c, p_access, "probe")
        print("4) provision_company OK: org", comp["org_id"], "email", comp["admin_email"])

        forged = forge_company_token(sub=real_admin_id, org_id=comp["org_id"])
        x1 = await c.get("/platform/me", headers=bearer(forged))
        print("5) DISCRIM company-aud token (sub=REAL admin id) -> /platform/me:",
              x1.status_code, "(expect 401 — guard is load-bearing)")

        pr = await c.post("/platform/refresh", json={"refresh_token": comp["admin_refresh"]})
        print("6a) company refresh -> /platform/refresh:", pr.status_code, "(expect 401)")
        ar = await c.post("/auth/refresh", json={"refresh_token": comp["admin_refresh"]})
        print("6b) same company refresh -> /auth/refresh (NOT revoked?):",
              ar.status_code, "(expect 200 — reject-without-revoke, AC3b)")

        ghost = forge_platform_token()  # random sub = unknown admin
        g = await c.get("/platform/me", headers=bearer(ghost))
        print("7) forged platform token (unknown sub) -> /platform/me:",
              g.status_code, "(expect 401 — token outlived/never-was an account)")


asyncio.run(main())
