async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        random_id = str(uuid4())
        r = await patch_legal_hold(c, p_access, random_id, True)
        print(f"PATCH /platform/orgs/{random_id}/legal-hold legal_hold=true")
        print(f"-> {r.status_code} (expect 404) body={r.text}")


asyncio.run(main())
