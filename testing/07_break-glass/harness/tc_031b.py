async def main() -> None:
    # Phase 2 of TC-BG-031: after the psql backdate of expires_at, re-read the inbox.
    grant_id = "03326d40-62dd-4574-817a-49f73dc9bd6f"
    admin_email = "admin-aea31-19e84623d1a4bac@oneai.dev"
    async with _client() as c:
        access, _ = await company_login_pair(c, admin_email, DEFAULT_PW)
        inbox = await company_inbox(c, access)
        g = next((x for x in inbox.json() if x["id"] == grant_id), None)
        print(f"3) inbox grant: status={g['status']} is_active={g['is_active']} "
              f"expires_at={g['expires_at']} -> is_active flipped, status unchanged")
        ok = g is not None and g["status"] == GRANT_APPROVED and g["is_active"] is False
        print("PASS is_active=false while status stays approved" if ok else "FAIL expiry not computed live")


asyncio.run(main())
