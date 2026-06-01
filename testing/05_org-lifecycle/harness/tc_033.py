async def main() -> None:
    async with _client() as c:
        p_access = await platform_login(c)
        comp = await provision_company(c, p_access, "contract33")
        oid = comp["org_id"]
        url = f"/platform/orgs/{oid}/status"
        h = bearer(p_access)

        # Invalid enum values -> 422 each (lowercase enum pinned).
        for bad in ["deleted", "ACTIVE", "Suspended", ""]:
            r = await c.patch(url, headers=h, json={"status": bad})
            print(f"status={bad!r} -> {r.status_code} body={r.text}")

        # Extra field -> 422 (extra=forbid).
        r = await c.patch(url, headers=h, json={"status": "active", "extra": "x"})
        print(f"extra_field -> {r.status_code} body={r.text}")

        # Missing status -> 422.
        r = await c.patch(url, headers=h, json={})
        print(f"missing_status -> {r.status_code} body={r.text}")

        # Leave the org on 'active' (provision leaves it active; no mutation succeeded).


asyncio.run(main())
