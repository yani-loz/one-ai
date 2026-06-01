async def main():
    # Onboard a fresh org and assert the 201 admin object carries NO password/hash field,
    # even though a password was just submitted. NEGATIVE-only: UserResponse legitimately
    # has 7 fields (id,email,full_name,role,is_active,org_id,created_at) — an exact-set
    # assertion would author a false FAIL, so we only forbid password-shaped leaks.
    prefix = f"cb052-{stamp()}"
    slug = f"{prefix}-{stamp()}"
    admin_email = f"admin-{slug}@oneai.dev"
    submitted_pw = DEFAULT_PW
    expected_admin_fields = {"id", "email", "full_name", "role", "is_active", "org_id", "created_at"}
    async with _client() as c:
        plat = await platform_login_pair(c)
        plat_token = plat[0]

        r = await onboard_org(
            c, plat_token, name=f"Org {slug}", slug=slug, admin_email=admin_email,
            admin_name="CB Onboard Admin", admin_pw=submitted_pw,
        )
        print("POST /platform/orgs ->", r.status_code)
        body = r.json()
        print("FULL 201 BODY:", body)

        admin = body.get("admin", {})
        admin_keys = set(admin.keys())
        print("ADMIN OBJECT KEYS:", sorted(admin_keys))

        # 1) No key matching /pass/ anywhere in the admin object.
        pass_keys_admin = [k for k in admin.keys() if "pass" in k.lower()]
        print("ADMIN KEYS MATCHING 'pass' (should be []):", pass_keys_admin)

        # 2) No key matching /pass/ anywhere in the ENTIRE 201 body (org + admin).
        import json as _json
        body_flat = _json.dumps(body).lower()
        pass_anywhere = "password" in body_flat or "password_hash" in body_flat
        print("'password' SUBSTRING ANYWHERE IN BODY (should be False):", pass_anywhere)

        # 3) The submitted plaintext password must not be echoed back.
        pw_echoed = submitted_pw.lower() in body_flat
        print("SUBMITTED PLAINTEXT PW ECHOED (should be False):", pw_echoed)

        # 4) No value looks like a bcrypt hash.
        looks_like_hash = any(str(v).startswith("$2") for v in admin.values())
        print("ANY ADMIN VALUE LOOKS LIKE A BCRYPT HASH (should be False):", looks_like_hash)

        # Characterize the admin shape (UserResponse) — expected 7 legit fields.
        print("ADMIN KEYS == expected UserResponse 7 fields:", admin_keys == expected_admin_fields)

        # Ground-truth marker: the onboarded org id, for psql verification of the stored hash.
        print("ONBOARDED org_id=", body.get("organization", {}).get("id"),
              "admin_email=", admin_email)

        verdict = (
            r.status_code == 201 and not pass_keys_admin and not pass_anywhere
            and not pw_echoed and not looks_like_hash
        )
        print("VERDICT:", "PASS — no password/hash in onboard response" if verdict else "FAIL")


asyncio.run(main())
