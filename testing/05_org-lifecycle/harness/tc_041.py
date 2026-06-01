async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract41")
        oid = comp["org_id"]  # my real org, so a 401 can only come from the token

        # alg='none' forged platform token.
        none_tok = forge_platform_token(alg="none")
        r1 = await get_org_detail(c, none_tok, oid)
        print(f"GET detail with alg=none token -> {r1.status_code} (expect 401) body={r1.text}")

        # Wrong-secret signed platform token.
        wrong_tok = forge_platform_token(secret="totally-wrong-secret-not-the-dev-default")
        r2 = await get_org_detail(c, wrong_tok, oid)
        print(f"GET detail with wrong-secret token -> {r2.status_code} (expect 401) body={r2.text}")


asyncio.run(main())
