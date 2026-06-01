async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        random_id = str(uuid4())
        r = await patch_status(c, p_access, random_id, STATUS_SUSPENDED)
        print(f"PATCH /platform/orgs/{random_id}/status status=suspended")
        print(f"-> {r.status_code} (expect 404) body={r.text}")


asyncio.run(main())
