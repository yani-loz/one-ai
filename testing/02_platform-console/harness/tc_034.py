async def main():
    async with _client() as c:
        # Correct aud + real demo admin sub; ONLY the signing secret is wrong.
        token = forge_platform_token(
            sub="609f2b17-bee9-4f7f-a26d-cb08f666497a", secret="not-the-real-secret"
        )
        r = await c.get("/platform/me", headers=bearer(token))
        print(f"WRONG-SECRET /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401
        print(f"assert_401: {'PASS' if ok else 'FAIL'} (got {r.status_code})")


asyncio.run(main())
