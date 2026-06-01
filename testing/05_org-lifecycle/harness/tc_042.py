async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract42")
        oid = comp["org_id"]  # my real org + valid body, so 401 isolates the token
        url = f"/platform/orgs/{oid}/status"

        # Expired (but otherwise valid dev-secret) platform token -> 401 not 500.
        exp_tok = forge_platform_token(expired=True)
        r1 = await c.patch(url, headers=bearer(exp_tok), json={"status": "active"})
        print(f"PATCH status with EXPIRED token -> {r1.status_code} (expect 401, never 500) body={r1.text}")

        # Valid-signature platform token whose sub is not a uuid -> 401 not 500.
        bad_sub_tok = forge_platform_token(sub="not-a-uuid")
        r2 = await c.patch(url, headers=bearer(bad_sub_tok), json={"status": "active"})
        print(f"PATCH status with sub='not-a-uuid' -> {r2.status_code} (expect 401, never 500) body={r2.text}")


asyncio.run(main())
