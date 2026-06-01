async def main():
    async with _client() as c:
        # Real demo admin sub so the ONLY reason for rejection is expiry (TokenExpiredError path).
        token = forge_platform_token(
            sub="609f2b17-bee9-4f7f-a26d-cb08f666497a", expired=True
        )
        decoded = jwt.decode(token, options={"verify_signature": False})
        print(f"forged exp(epoch)={decoded['exp']} iat(epoch)={decoded['iat']} (both in the past)")
        r = await c.get("/platform/me", headers=bearer(token))
        print(f"EXPIRED /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401
        print(f"assert_401: {'PASS' if ok else 'FAIL'} (got {r.status_code})")


asyncio.run(main())
