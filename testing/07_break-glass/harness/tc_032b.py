async def main() -> None:
    # Phase 2 of TC-BG-032: after psql-backdate, confirm expired then attempt re-approve.
    grant_id = "65261f13-4b7e-47bf-8e44-b33b08d9e6d6"
    admin_email = "admin-aea32-19e8462f7d60b12@oneai.dev"
    async with _client() as c:
        access, _ = await company_login_pair(c, admin_email, DEFAULT_PW)

        inbox = await company_inbox(c, access)
        g = next((x for x in inbox.json() if x["id"] == grant_id), None)
        print(f"4) inbox after backdate: status={g['status']} is_active={g['is_active']} "
              f"expires_at={g['expires_at']}")

        re_ap = await company_approve(c, access, grant_id)
        print(f"5) re-approve expired grant: {re_ap.status_code} body={re_ap.text}")

        inbox2 = await company_inbox(c, access)
        g2 = next((x for x in inbox2.json() if x["id"] == grant_id), None)
        print(f"6) inbox after rejected re-approve: status={g2['status']} is_active={g2['is_active']} "
              f"expires_at={g2['expires_at']} (should be UNCHANGED)")

        ok = (re_ap.status_code == 409
              and g2["status"] == GRANT_APPROVED
              and g2["is_active"] is False
              and g2["expires_at"] == g["expires_at"])
        print("PASS expired grant is terminal — 409, expires_at not re-stamped"
              if ok else "FAIL expired grant resurrected")


asyncio.run(main())
