async def main():
    # GET /platform/me must return EXACTLY {id,email,full_name} for the demo platform admin.
    allowed = {"id", "email", "full_name"}
    forbidden = ("password", "org_id", "is_active", "created_at", "updated_at", "role")
    async with _client() as c:
        plat = await platform_login_pair(c)
        plat_token = plat[0]

        r = await c.get("/platform/me", headers=bearer(plat_token))
        print("GET /platform/me ->", r.status_code)
        body = r.json()
        print("BODY:", body)
        keyset = set(body.keys())
        print("KEYSET:", sorted(keyset))

        exact = keyset == allowed
        print("EXACT 3-KEY SET (id,email,full_name):", exact)

        forbidden_hits = []
        for k in body.keys():
            kl = k.lower()
            for bad in forbidden:
                if bad in kl:
                    forbidden_hits.append({"key": k, "matched": bad})
        print("FORBIDDEN-KEY HITS (should be []):", forbidden_hits)

        # No value should look like a bcrypt hash ($2b$...) even if a key were renamed.
        looks_like_hash = any(str(v).startswith("$2") for v in body.values())
        print("ANY VALUE LOOKS LIKE A BCRYPT HASH (should be False):", looks_like_hash)

        verdict = exact and not forbidden_hits and not looks_like_hash
        print("VERDICT:", "PASS — identity-only, no hash/org/flags leaked" if verdict else "FAIL")


asyncio.run(main())
