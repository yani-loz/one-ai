async def main():
    async with _client() as c:
        # Valid signature + correct aud='platform', but sub is not a UUID -> _principal_from_claims
        # must catch ValueError -> TokenInvalidError -> 401, NOT a 500.
        token = forge_platform_token(sub="not-a-uuid")
        print(f"forged sub={jwt.decode(token, options={'verify_signature': False})['sub']!r}")
        r = await c.get("/platform/me", headers=bearer(token))
        print(f"MALFORMED-SUB /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401
        no500 = r.status_code != 500
        print(f"assert_401_not_500: {'PASS' if ok and no500 else 'FAIL'} (got {r.status_code})")


asyncio.run(main())
