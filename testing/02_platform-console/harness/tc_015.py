async def main() -> None:
    """TC-PC-015 — email canonicalization on /platform/login (DYN-02).

    Logging in the demo admin with an UPPERCASE email 'SUPER@ETHERA.AI' + the correct
    password must → 200, proving NormalizedEmail lowercases the whole address (local-part
    included) so case variants resolve to ONE identity. We also confirm a mixed-case variant
    works and that the resolved `sub` (admin id) is IDENTICAL across the lowercase and
    uppercase logins — i.e. it is the same account, not a coincidental second match. Read
    only: only correct passwords are sent (the demo admin is never mutated)."""
    upper = PLATFORM_EMAIL.upper()                 # SUPER@ETHERA.AI
    mixed = "Super@Ethera.AI"

    def sub_of(access_token: str) -> str:
        claims = jwt.decode(access_token, DEV_SECRET, algorithms=[ALG], audience=PLATFORM_AUD)
        return str(claims["sub"])

    async with _client() as c:
        r_lower = await c.post("/platform/login", json={"email": PLATFORM_EMAIL, "password": PLATFORM_PW})
        r_upper = await c.post("/platform/login", json={"email": upper, "password": PLATFORM_PW})
        r_mixed = await c.post("/platform/login", json={"email": mixed, "password": PLATFORM_PW})

        print(f"lower_email={PLATFORM_EMAIL!r} status={r_lower.status_code}")
        print(f"upper_email={upper!r} status={r_upper.status_code}")
        print(f"mixed_email={mixed!r} status={r_mixed.status_code}")

        all_200 = r_lower.status_code == r_upper.status_code == r_mixed.status_code == 200
        same_identity = False
        if all_200:
            sub_lower = sub_of(r_lower.json()["access_token"])
            sub_upper = sub_of(r_upper.json()["access_token"])
            sub_mixed = sub_of(r_mixed.json()["access_token"])
            print(f"sub_lower={sub_lower}")
            print(f"sub_upper={sub_upper}")
            print(f"sub_mixed={sub_mixed}")
            same_identity = sub_lower == sub_upper == sub_mixed
            # platform body must still exclude `user`
            print(f"upper_has_user_field={'user' in r_upper.json()}")

        print(f"all_three_200={all_200} same_admin_id_across_case={same_identity}")
        ok = all_200 and same_identity
        print(f"VERDICT={'PASS' if ok else 'FAIL'}")


asyncio.run(main())
