async def main():
    async with _client() as c:
        token = forge_platform_token(alg="none")
        r = await c.get("/platform/me", headers=bearer(token))
        print(f"ALG=NONE token (header alg): {jwt.get_unverified_header(token)}")
        print(f"ALG=NONE /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401
        print(f"assert_401: {'PASS' if ok else 'FAIL'} (got {r.status_code})")


asyncio.run(main())
