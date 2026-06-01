async def main():
    async with _client() as c:
        r = await c.get("/platform/me")
        print(f"NO-HEADER /platform/me -> {r.status_code} {r.text}")
        ok = r.status_code == 401
        print(f"assert_401_not_403_500: {'PASS' if ok else 'FAIL'} (got {r.status_code})")


asyncio.run(main())
