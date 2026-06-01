async def main():
    sub = "609f2b17-bee9-4f7f-a26d-cb08f666497a"
    async with _client() as c:
        results = {}
        for claim in ("aud", "exp", "sub"):
            token = forge_platform_token(sub=sub, drop=(claim,))
            present = list(jwt.decode(token, options={"verify_signature": False}).keys())
            r = await c.get("/platform/me", headers=bearer(token))
            results[claim] = r.status_code
            print(f"DROP {claim!r} (claims present={present}) /platform/me -> {r.status_code} {r.text}")
        ok = all(code == 401 for code in results.values())
        print(f"assert_all_401: {'PASS' if ok else 'FAIL'} {results}")


asyncio.run(main())
