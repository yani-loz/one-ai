async def main():
    # Positive: user_count accuracy without content leak. Onboard an org (admin=1), then as
    # its company_admin create 2 members (total 3), then GET /platform/orgs and assert THAT
    # org's user_count == 3 while the row still exposes only the 6 metadata fields.
    prefix = f"cb053-{stamp()}"
    allowed = {"id", "name", "slug", "status", "user_count", "created_at"}
    async with _client() as c:
        plat = await platform_login_pair(c)
        plat_token = plat[0]

        company = await provision_company(c, plat_token, prefix)
        our_org_id = company["org_id"]
        admin_access = company["admin_access"]
        print("PROVISIONED org_id=", our_org_id, "(admin counts as user #1)")

        # Create 2 members via the company-admin token, namespaced with our prefix+stamp.
        created = []
        for i in range(2):
            u_email = f"member-{i}-{prefix}-{stamp()}@oneai.dev"
            cr = await c.post(
                "/users",
                headers=bearer(admin_access),
                json={
                    "email": u_email,
                    "full_name": f"CB Member {i}",
                    "role": "member",
                    "password": DEFAULT_PW,
                },
            )
            created.append((u_email, cr.status_code))
            print(f"POST /users [{i}] {u_email} ->", cr.status_code, cr.json())
        print("CREATED:", created)

        r = await c.get("/platform/orgs", headers=bearer(plat_token))
        print("GET /platform/orgs ->", r.status_code)
        rows = r.json()
        our_row = next((row for row in rows if row.get("id") == our_org_id), None)
        print("OUR ROW:", our_row)

        if our_row is None:
            print("VERDICT: FAIL-HARNESS our org not found")
            return

        count_ok = our_row.get("user_count") == 3
        print("user_count == 3:", count_ok, "(actual:", our_row.get("user_count"), ")")

        keyset_ok = set(our_row.keys()) == allowed
        print("ROW KEYSET == 6 metadata fields:", keyset_ok, sorted(our_row.keys()))

        # No per-user data (emails/names of the members) bleeds into the row.
        row_values_str = " ".join(str(v) for v in our_row.values()).lower()
        per_user_leak = any(email.lower() in row_values_str for email, _ in created)
        print("PER-USER EMAIL LEAKS INTO ROW (should be False):", per_user_leak)

        verdict = count_ok and keyset_ok and not per_user_leak
        print("VERDICT:", "PASS — accurate count, metadata-only" if verdict else "FAIL")


asyncio.run(main())
