async def main() -> None:
    async with _client() as c:
        # Valid token so the ONLY variable is the malformed path (isolate from auth 401).
        p_access = await platform_login(c)
        for bad in ["not-a-uuid", "12345", "deadbeef", "abc-def-ghi"]:
            r = await c.get(f"/platform/orgs/{bad}", headers=bearer(p_access))
            print(f"GET /platform/orgs/{bad} -> status={r.status_code} body={r.text}")


asyncio.run(main())
