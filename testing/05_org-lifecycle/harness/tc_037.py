async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract37")
        oid = comp["org_id"]
        url = f"/platform/orgs/{oid}/legal-hold"
        h = bearer(p_access)

        # Record ACTUAL codes for each input (Pydantic v2 lax-bool coercion is the variable).
        for val in ["yes", 2, "maybe", "true", 1, False]:
            r = await c.patch(url, headers=h, json={"legal_hold": val})
            echoed = r.json().get("legal_hold") if r.status_code == 200 else None
            print(f"legal_hold={val!r} -> {r.status_code} echoed={echoed} body={r.text[:160]}")

        # Extra field -> 422 (extra=forbid).
        r = await c.patch(url, headers=h, json={"legal_hold": True, "extra": "x"})
        print(f"extra_field -> {r.status_code} body={r.text[:160]}")

        # Missing legal_hold -> 422.
        r = await c.patch(url, headers=h, json={})
        print(f"missing_legal_hold -> {r.status_code} body={r.text[:160]}")

        # Restore to false (clean state).
        await patch_legal_hold(c, p_access, oid, False)


asyncio.run(main())
