async def main():
    import json
    async with _client() as c:
        plat = await platform_login(c)

        # 1. Provision org X with some history.
        org = await provision_company(c, plat, "retain-er021")
        org_id, slug = org["org_id"], org["slug"]
        print("=== TC-ER-021 ===")
        print("ORG_ID:", org_id)
        print("SLUG:", slug)

        rs = await request_support(c, plat, org_id)
        grant_id = rs.json()["id"]
        ap = await company_approve(c, org["admin_access"], grant_id)
        print("SETUP request/approve:", rs.status_code, ap.status_code)

        # 2. Compliance export.
        ex = await compliance_export(c, plat, org_id)
        print("EXPORT:", ex.status_code)
        body = ex.json()

        org_block = body.get("organization", {})
        expected_fields = {"id", "name", "slug", "status", "user_count", "legal_hold", "created_at"}
        print("ORG_FIELDS:", sorted(org_block.keys()))
        print("ORG_FIELDS_MATCH:", set(org_block.keys()) == expected_fields)
        print("LEGAL_HOLD PRESENT:", "legal_hold" in org_block, "value:", org_block.get("legal_hold"))
        print("AUDIT IS LIST:", isinstance(body.get("audit"), list),
              "len:", len(body.get("audit", [])))
        print("AUDIT ACTIONS:", [e["action"] for e in body.get("audit", [])])
        print("GENERATED_AT PRESENT:", "generated_at" in body, "value:", body.get("generated_at"))

        # 3. Content-blind secret-substring scan over the FULL serialized body.
        blob = json.dumps(body).lower()
        forbidden = ["password", "hash", "bcrypt", "$2b$", "token", "secret", "eyj", "bearer",
                     PLATFORM_PW.lower(), DEFAULT_PW.lower()]
        hits = [s for s in forbidden if s in blob]
        print("SECRET_SUBSTRING_HITS:", hits)
        sample_email = next((e.get("actor_email") for e in body.get("audit", [])
                             if e.get("actor_email")), None)
        print("RETAINED actor_email (documented, expected):", sample_email)

        ok = (ex.status_code == 200
              and set(org_block.keys()) == expected_fields
              and isinstance(body.get("audit"), list) and len(body["audit"]) > 0
              and "generated_at" in body
              and not hits)
        print("RESULT:", "PASS" if ok else "FAIL")


asyncio.run(main())
