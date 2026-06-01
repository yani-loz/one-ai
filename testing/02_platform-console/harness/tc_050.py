async def main():
    # CB suite run-stamp namespace — every org/email prefixed so the shared DB stays isolated.
    prefix = f"cb050-{stamp()}"
    allowed = {"id", "name", "slug", "status", "user_count", "created_at"}
    forbidden_substrings = (
        "content", "message", "conversation", "memory",
        "cost", "token", "usage", "password", "admin_email", "email",
    )
    async with _client() as c:
        plat = await platform_login_pair(c)
        plat_token = plat[0]

        # Provision OUR OWN org so we KNOW sensitive data (admin_email + password_hash)
        # exists adjacent to its row in the DB — the discriminating control.
        company = await provision_company(c, plat_token, prefix)
        our_org_id = company["org_id"]
        our_email = company["admin_email"]
        print("PROVISIONED org_id=", our_org_id, "admin_email=", our_email)

        r = await c.get("/platform/orgs", headers=bearer(plat_token))
        print("GET /platform/orgs ->", r.status_code)
        rows = r.json()
        print("total rows visible:", len(rows))

        # Locate OUR row by org_id (shared DB — never trust list position/length).
        our_row = next((row for row in rows if row.get("id") == our_org_id), None)
        print("OUR ROW:", our_row)

        if our_row is None:
            print("VERDICT: FAIL-HARNESS our provisioned org not found in list")
            return

        # 1) Every row must have EXACTLY the 6 metadata keys — no extras.
        bad_rows = []
        for row in rows:
            keyset = set(row.keys())
            if keyset != allowed:
                bad_rows.append({"id": row.get("id"), "extra": sorted(keyset - allowed),
                                 "missing": sorted(allowed - keyset)})
        print("ROWS WITH NON-EXACT KEYSET (should be []):", bad_rows)

        # 2) Specifically scan every key across all rows for forbidden substrings.
        forbidden_hits = []
        for row in rows:
            for k in row.keys():
                kl = k.lower()
                for bad in forbidden_substrings:
                    if bad in kl:
                        forbidden_hits.append({"row_id": row.get("id"), "key": k, "matched": bad})
        print("FORBIDDEN-SUBSTRING KEY HITS (should be []):", forbidden_hits)

        # 3) Discriminating assertion: our org's known admin_email is NOT in our row's values.
        our_values_str = " ".join(str(v) for v in our_row.values()).lower()
        leaks_email = our_email.lower() in our_values_str
        print("OUR admin_email LEAKS INTO OUR ROW VALUES (should be False):", leaks_email)

        verdict = (
            not bad_rows and not forbidden_hits and not leaks_email
            and set(our_row.keys()) == allowed
        )
        print("VERDICT:", "PASS — metadata-only, no sensitive key/value leak" if verdict else "FAIL")


asyncio.run(main())
