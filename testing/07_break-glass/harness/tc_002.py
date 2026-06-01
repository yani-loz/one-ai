

async def main():
    async with _client() as c:
        plat = await platform_login(c)
        org = await provision_company(c, plat, "consent-bg002")
        org_id = org["org_id"]
        admin_token = org["admin_access"]
        print("== ORG ==", org_id, org["slug"])

        # 1. Create a requested grant.
        r = await request_support(c, plat, org_id)
        grant_id = r.json()["id"]
        print("== REQUEST status ==", r.status_code, "grant", grant_id)

        # 2. Enumerate the LIVE OpenAPI routes.
        spec = (await c.get("/openapi.json")).json()
        paths = sorted(spec["paths"].keys())
        approve_paths = [p for p in paths if "approve" in p]
        platform_paths = [p for p in paths if p.startswith("/platform")]
        print("== APPROVE ROUTES (live OpenAPI) ==", approve_paths)
        print("== /platform/* ROUTES ==")
        for p in platform_paths:
            print("   ", p, sorted(spec["paths"][p].keys()))
        platform_approve = [p for p in platform_paths if "approve" in p]
        print("== /platform/* containing 'approve' ==", platform_approve)

        # 3. Adversarial probe: a plausible platform approve URL with the REAL platform token.
        probe = await c.post(
            f"/platform/support-requests/{grant_id}/approve", headers=bearer(plat)
        )
        print("== PROBE POST /platform/support-requests/{id}/approve ==", probe.status_code)

        # 4. Positive control: the company approve endpoint IS the path to 'approved'.
        ok = await company_approve(c, admin_token, grant_id)
        print("== COMPANY approve (real admin) ==", ok.status_code, ok.json()["status"])

asyncio.run(main())
