async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        random_id = str(uuid4())
        det = await get_org_detail(c, p_access, random_id)
        print(f"GET /platform/orgs/{random_id}")
        print(f"status={det.status_code} (expect 404)")
        print(f"body={det.text}")


asyncio.run(main())
